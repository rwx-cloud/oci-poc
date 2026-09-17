"""Idempotently provision the VCN, subnet, gateway and rules the benchmark needs.

Writes network.json describing the subnet and availability domain to use. Running
this repeatedly is a no-op once the VCN exists, so the benchmark never pays for
network setup in its measured window.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import oci

from oci_common import (
    POC_TAG,
    clients,
    compartment_id,
    die,
    freeform_tags,
    log,
    write_json,
)

VCN_NAME = "rwx-oci-poc-vcn"
VCN_CIDR = "10.0.0.0/16"
SUBNET_NAME = "rwx-oci-poc-public-subnet"
SUBNET_CIDR = "10.0.0.0/24"
IG_NAME = "rwx-oci-poc-ig"
ROUTE_TABLE_NAME = "rwx-oci-poc-rt"
SECURITY_LIST_NAME = "rwx-oci-poc-sl"


def find(items, name):
    for item in items:
        if item.display_name == name and item.lifecycle_state not in (
            "TERMINATED",
            "TERMINATING",
        ):
            return item
    return None


def ensure_vcn(network, compartment):
    existing = find(
        oci.pagination.list_call_get_all_results(
            network.list_vcns, compartment
        ).data,
        VCN_NAME,
    )
    if existing:
        log(f"vcn exists: {existing.id}")
        return existing

    log("creating vcn")
    vcn = network.create_vcn(
        oci.core.models.CreateVcnDetails(
            compartment_id=compartment,
            cidr_block=VCN_CIDR,
            display_name=VCN_NAME,
            dns_label="rwxocipoc",
            freeform_tags=freeform_tags(),
        )
    ).data
    oci.wait_until(
        network, network.get_vcn(vcn.id), "lifecycle_state", "AVAILABLE",
        max_wait_seconds=300,
    )
    log(f"created vcn: {vcn.id}")
    return vcn


def ensure_internet_gateway(network, compartment, vcn):
    existing = find(
        oci.pagination.list_call_get_all_results(
            network.list_internet_gateways, compartment, vcn_id=vcn.id
        ).data,
        IG_NAME,
    )
    if existing:
        log(f"internet gateway exists: {existing.id}")
        return existing

    log("creating internet gateway")
    gateway = network.create_internet_gateway(
        oci.core.models.CreateInternetGatewayDetails(
            compartment_id=compartment,
            vcn_id=vcn.id,
            is_enabled=True,
            display_name=IG_NAME,
            freeform_tags=freeform_tags(),
        )
    ).data
    oci.wait_until(
        network,
        network.get_internet_gateway(gateway.id),
        "lifecycle_state",
        "AVAILABLE",
        max_wait_seconds=300,
    )
    log(f"created internet gateway: {gateway.id}")
    return gateway


def ensure_route_table(network, compartment, vcn, gateway):
    rule = oci.core.models.RouteRule(
        destination="0.0.0.0/0",
        destination_type="CIDR_BLOCK",
        network_entity_id=gateway.id,
        description="default route to the internet",
    )

    existing = find(
        oci.pagination.list_call_get_all_results(
            network.list_route_tables, compartment, vcn_id=vcn.id
        ).data,
        ROUTE_TABLE_NAME,
    )
    if existing:
        log(f"route table exists: {existing.id}")
        return existing

    log("creating route table")
    table = network.create_route_table(
        oci.core.models.CreateRouteTableDetails(
            compartment_id=compartment,
            vcn_id=vcn.id,
            display_name=ROUTE_TABLE_NAME,
            route_rules=[rule],
            freeform_tags=freeform_tags(),
        )
    ).data
    oci.wait_until(
        network, network.get_route_table(table.id), "lifecycle_state", "AVAILABLE",
        max_wait_seconds=300,
    )
    log(f"created route table: {table.id}")
    return table


def ensure_security_list(network, compartment, vcn):
    existing = find(
        oci.pagination.list_call_get_all_results(
            network.list_security_lists, compartment, vcn_id=vcn.id
        ).data,
        SECURITY_LIST_NAME,
    )
    if existing:
        log(f"security list exists: {existing.id}")
        return existing

    log("creating security list")
    ingress = [
        oci.core.models.IngressSecurityRule(
            protocol="6",  # TCP
            source="0.0.0.0/0",
            source_type="CIDR_BLOCK",
            tcp_options=oci.core.models.TcpOptions(
                destination_port_range=oci.core.models.PortRange(min=22, max=22)
            ),
            description="ssh, used to measure when the guest is actually usable",
        ),
        oci.core.models.IngressSecurityRule(
            protocol="1",  # ICMP
            source="0.0.0.0/0",
            source_type="CIDR_BLOCK",
            icmp_options=oci.core.models.IcmpOptions(type=3, code=4),
            description="path mtu discovery",
        ),
    ]
    egress = [
        oci.core.models.EgressSecurityRule(
            protocol="all",
            destination="0.0.0.0/0",
            destination_type="CIDR_BLOCK",
            description="allow all egress",
        )
    ]
    security_list = network.create_security_list(
        oci.core.models.CreateSecurityListDetails(
            compartment_id=compartment,
            vcn_id=vcn.id,
            display_name=SECURITY_LIST_NAME,
            ingress_security_rules=ingress,
            egress_security_rules=egress,
            freeform_tags=freeform_tags(),
        )
    ).data
    oci.wait_until(
        network,
        network.get_security_list(security_list.id),
        "lifecycle_state",
        "AVAILABLE",
        max_wait_seconds=300,
    )
    log(f"created security list: {security_list.id}")
    return security_list


def ensure_subnet(network, compartment, vcn, route_table, security_list):
    existing = find(
        oci.pagination.list_call_get_all_results(
            network.list_subnets, compartment, vcn_id=vcn.id
        ).data,
        SUBNET_NAME,
    )
    if existing:
        log(f"subnet exists: {existing.id}")
        return existing

    log("creating subnet")
    subnet = network.create_subnet(
        oci.core.models.CreateSubnetDetails(
            compartment_id=compartment,
            vcn_id=vcn.id,
            cidr_block=SUBNET_CIDR,
            display_name=SUBNET_NAME,
            dns_label="public",
            route_table_id=route_table.id,
            security_list_ids=[security_list.id],
            prohibit_public_ip_on_vnic=False,
            freeform_tags=freeform_tags(),
        )
    ).data
    oci.wait_until(
        network, network.get_subnet(subnet.id), "lifecycle_state", "AVAILABLE",
        max_wait_seconds=300,
    )
    log(f"created subnet: {subnet.id}")
    return subnet


def main():
    compartment = compartment_id()
    c = clients()
    network = c["network"]

    domains = c["identity"].list_availability_domains(compartment).data
    if not domains:
        die("no availability domains in this compartment")

    vcn = ensure_vcn(network, compartment)
    gateway = ensure_internet_gateway(network, compartment, vcn)
    route_table = ensure_route_table(network, compartment, vcn, gateway)
    security_list = ensure_security_list(network, compartment, vcn)
    subnet = ensure_subnet(network, compartment, vcn, route_table, security_list)

    write_json(
        "network.json",
        {
            "availability_domains": [d.name for d in domains],
            "compartment_id": compartment,
            "subnet_id": subnet.id,
            "tag": POC_TAG,
            "vcn_id": vcn.id,
        },
    )


if __name__ == "__main__":
    main()
