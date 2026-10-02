#!/usr/bin/env python3
"""Start an AWS EC2 instance and wait until its state is running.

Usage:
  python start_ec2_instance.py --target https://myapp.example.com --region ap-south-1
  python start_ec2_instance.py --target 203.0.113.10 --region ap-south-1
  python start_ec2_instance.py --instance-id i-0123456789abcdef0 --region ap-south-1
  python start_ec2_instance.py --target 203.0.113.10 --dry-run
"""

from __future__ import annotations

import argparse
import re
import socket
import sys
from typing import Any
from urllib.parse import urlparse

INSTANCE_ID_PATTERN = re.compile(r"^i-[0-9a-f]{8,17}$")
IPV4_PATTERN = re.compile(
    r"^(?:(?:25[0-5]|2[0-4]\d|[01]?\d?\d)\.){3}"
    r"(?:25[0-5]|2[0-4]\d|[01]?\d?\d)$"
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Start an EC2 instance by instance ID, IP, or app URL/hostname."
    )
    target_group = parser.add_mutually_exclusive_group(required=True)
    target_group.add_argument(
        "--instance-id",
        help="EC2 instance ID, for example i-0123456789abcdef0 (admin use)",
    )
    target_group.add_argument(
        "--target",
        help="App URL (https://host/...), hostname, or IPv4 address — resolved to an instance in the region",
    )
    parser.add_argument(
        "--region",
        default=None,
        help="AWS region, for example ap-south-1. Uses AWS_DEFAULT_REGION if omitted.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate and resolve target; do not start unless already running.",
    )
    parser.add_argument(
        "--wait-timeout",
        type=int,
        default=300,
        help="Seconds to wait for the instance to become running (default: 300).",
    )
    return parser.parse_args()


def validate_instance_id(instance_id: str) -> None:
    if not INSTANCE_ID_PATTERN.match(instance_id):
        raise ValueError(
            f"Invalid instance id '{instance_id}'. "
            "Expected a value like i-0123456789abcdef0"
        )


def extract_hostname(raw: str) -> str:
    value = raw.strip()
    if not value:
        raise ValueError("Target is empty.")
    if "://" in value:
        parsed = urlparse(value)
        host = parsed.hostname
    else:
        host = value.split("/")[0].split(":")[0].strip()
    if not host:
        raise ValueError(f"Could not parse a hostname from '{raw}'.")
    return host


def resolve_to_ipv4(raw: str) -> str:
    host = extract_hostname(raw)
    if IPV4_PATTERN.match(host):
        return host
    try:
        results = socket.getaddrinfo(host, None, family=socket.AF_INET, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise ValueError(f"Could not resolve hostname '{host}': {exc}") from exc
    if not results:
        raise ValueError(f"Could not resolve hostname '{host}' to an IPv4 address.")
    ip = results[0][4][0]
    print(f"Resolved '{raw}' -> {host} -> {ip}")
    return ip


def find_instance_id_by_ip(ec2: Any, ip: str) -> str:
    instance_ids: list[str] = []
    for filter_name in ("ip-address", "private-ip-address"):
        response = ec2.describe_instances(
            Filters=[{"Name": filter_name, "Values": [ip]}]
        )
        for reservation in response.get("Reservations") or []:
            for instance in reservation.get("Instances") or []:
                iid = instance.get("InstanceId")
                if iid and iid not in instance_ids:
                    instance_ids.append(iid)

    if not instance_ids:
        raise RuntimeError(
            f"No EC2 instance in this region has public or private IP {ip}. "
            "Check AWS_REGION, or use --instance-id if the app uses a load balancer "
            "(URLs that point to ELB/CloudFront cannot be mapped to a single EC2)."
        )
    if len(instance_ids) > 1:
        raise RuntimeError(
            f"Multiple instances match IP {ip}: {', '.join(instance_ids)}. "
            "Contact DevOps or use --instance-id."
        )
    print(f"Matched IP {ip} to instance {instance_ids[0]}")
    return instance_ids[0]


def resolve_instance_id(
    ec2: Any,
    instance_id: str | None,
    target: str | None,
) -> str:
    if instance_id:
        validate_instance_id(instance_id.strip())
        return instance_id.strip()
    assert target is not None
    ip = resolve_to_ipv4(target)
    return find_instance_id_by_ip(ec2, ip)


def describe_instance(ec2: Any, instance_id: str) -> dict[str, Any]:
    response = ec2.describe_instances(InstanceIds=[instance_id])
    reservations = response.get("Reservations") or []
    if not reservations or not reservations[0].get("Instances"):
        raise RuntimeError(f"Instance {instance_id} was not found.")
    return reservations[0]["Instances"][0]


def instance_state_name(instance: dict[str, Any]) -> str:
    return instance.get("State", {}).get("Name", "unknown")


def start_instance(
    instance_id: str | None,
    target: str | None,
    region: str | None,
    dry_run: bool,
    wait_timeout: int,
) -> int:
    try:
        import boto3
        from botocore.exceptions import BotoCoreError, ClientError, NoCredentialsError, WaiterError
    except ImportError:
        print("boto3 is not installed. Run: pip install -r requirements.txt", file=sys.stderr)
        return 2

    session_kwargs = {}
    if region:
        session_kwargs["region_name"] = region

    try:
        session = boto3.Session(**session_kwargs)
        ec2 = session.client("ec2")
        current_region = session.region_name or "not set"
        print(f"Using AWS region: {current_region}")

        resolved_id = resolve_instance_id(ec2, instance_id, target)
        instance = describe_instance(ec2, resolved_id)
        current_state = instance_state_name(instance)
        print(f"Instance {resolved_id} current state: {current_state}")

        if current_state == "running":
            print("Instance is already running. No start required.")
            return 0

        if current_state not in {"stopped", "stopping"}:
            print(
                f"Cannot start instance from state '{current_state}'. "
                "EC2 can only be started from stopped (or while stopping).",
                file=sys.stderr,
            )
            return 1

        if dry_run:
            print("Dry run: would call StartInstances and wait until running.")
            return 0

        if current_state == "stopping":
            print("Instance is stopping. Waiting until it is stopped before starting...")
            waiter = ec2.get_waiter("instance_stopped")
            waiter.wait(
                InstanceIds=[resolved_id],
                WaiterConfig={"Delay": 15, "MaxAttempts": max(1, wait_timeout // 15)},
            )

        print(f"Starting instance {resolved_id}...")
        start_response = ec2.start_instances(InstanceIds=[resolved_id])
        starting_states = start_response.get("StartingInstances") or []
        if starting_states:
            previous = starting_states[0].get("PreviousState", {}).get("Name")
            current = starting_states[0].get("CurrentState", {}).get("Name")
            print(f"Start requested. Previous state: {previous}. Current state: {current}")

        waiter = ec2.get_waiter("instance_running")
        waiter.wait(
            InstanceIds=[resolved_id],
            WaiterConfig={"Delay": 15, "MaxAttempts": max(1, wait_timeout // 15)},
        )

        instance = describe_instance(ec2, resolved_id)
        final_state = instance_state_name(instance)
        print(f"Instance {resolved_id} is now {final_state}.")
        if instance.get("PublicIpAddress"):
            print(f"Public IP: {instance['PublicIpAddress']}")
        if instance.get("PrivateIpAddress"):
            print(f"Private IP: {instance['PrivateIpAddress']}")
        return 0 if final_state == "running" else 1

    except NoCredentialsError:
        print(
            "AWS credentials were not found. Configure them with AWS_ACCESS_KEY_ID "
            "and AWS_SECRET_ACCESS_KEY, or an IAM role on the Jenkins agent.",
            file=sys.stderr,
        )
        return 2
    except WaiterError as exc:
        print(f"Timed out waiting for the instance to change state: {exc}", file=sys.stderr)
        return 1
    except ClientError as exc:
        error = exc.response.get("Error", {})
        print(
            f"AWS API error ({error.get('Code', 'Unknown')}): {error.get('Message', str(exc))}",
            file=sys.stderr,
        )
        return 1
    except (BotoCoreError, ValueError, RuntimeError) as exc:
        print(str(exc), file=sys.stderr)
        return 1


def main() -> int:
    args = parse_args()
    return start_instance(
        instance_id=args.instance_id.strip() if args.instance_id else None,
        target=args.target.strip() if args.target else None,
        region=args.region,
        dry_run=args.dry_run,
        wait_timeout=args.wait_timeout,
    )


if __name__ == "__main__":
    sys.exit(main())
