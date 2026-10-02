#!/usr/bin/env python3

"""
Start an AWS EC2 instance using an application URL, hostname, IPv4 address,
or directly using an EC2 instance ID.

Examples:

    python start_ec2_instance.py --target https://myapp.example.com --region ap-south-1

    python start_ec2_instance.py --target myapp.example.com --region ap-south-1

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
        description=(
            "Start an EC2 instance using an application URL, "
            "hostname, IPv4 address, or instance ID."
        )
    )

    target_group = parser.add_mutually_exclusive_group(required=True)

    target_group.add_argument(
        "--target",
        help=(
            "Application URL, hostname, or IPv4 address. "
            "The target is resolved to an EC2 instance."
        ),
    )

    target_group.add_argument(
        "--instance-id",
        help=(
            "EC2 instance ID, for example "
            "i-0123456789abcdef0. DevOps/admin use only."
        ),
    )

    parser.add_argument(
        "--region",
        default=None,
        help=(
            "AWS region, for example ap-south-1. "
            "Uses AWS_DEFAULT_REGION if omitted."
        ),
    )

    parser.add_argument(
        "--dry-run",
        action="store_true",
        help=(
            "Resolve and validate the target but do not start "
            "the EC2 instance."
        ),
    )

    parser.add_argument(
        "--wait-timeout",
        type=int,
        default=300,
        help=(
            "Maximum number of seconds to wait for the instance "
            "to become running. Default: 300."
        ),
    )

    return parser.parse_args()


def validate_instance_id(instance_id: str) -> None:
    """
    Validate EC2 instance ID format.
    """

    if not INSTANCE_ID_PATTERN.match(instance_id):
        raise ValueError(
            f"Invalid instance ID '{instance_id}'. "
            "Expected a value like i-0123456789abcdef0."
        )


def extract_hostname(raw: str) -> str:
    """
    Extract hostname from:

        https://example.com
        http://example.com/path
        example.com
        example.com/path
        example.com:443
        192.168.1.10
    """

    value = raw.strip()

    if not value:
        raise ValueError("Application URL/IP cannot be empty.")

    # URL with scheme
    if "://" in value:
        parsed = urlparse(value)

        host = parsed.hostname

        if not host:
            raise ValueError(
                f"Could not extract hostname from '{raw}'."
            )

    else:
        # Add a temporary scheme so urlparse handles
        # hostname/path/port consistently.
        parsed = urlparse(f"//{value}")

        host = parsed.hostname

        if not host:
            raise ValueError(
                f"Could not extract hostname from '{raw}'."
            )

    return host.strip()


def resolve_to_ipv4(raw: str) -> str:
    """
    Convert an application URL/hostname/IP into an IPv4 address.

    If the input is already an IPv4 address, it is returned directly.

    If the input is a hostname or URL, DNS resolution is performed.
    """

    host = extract_hostname(raw)

    # Already an IPv4 address
    if IPV4_PATTERN.match(host):
        print(f"Target is already an IPv4 address: {host}")
        return host

    print(f"Resolving hostname: {host}")

    try:
        results = socket.getaddrinfo(
            host,
            None,
            family=socket.AF_INET,
            type=socket.SOCK_STREAM,
        )

    except socket.gaierror as exc:
        raise ValueError(
            f"Could not resolve hostname '{host}' to an IPv4 address: {exc}"
        ) from exc

    if not results:
        raise ValueError(
            f"Could not resolve hostname '{host}' to an IPv4 address."
        )

    # Remove duplicate IP addresses while preserving order.
    resolved_ips = []

    for result in results:
        ip = result[4][0]

        if ip not in resolved_ips:
            resolved_ips.append(ip)

    if not resolved_ips:
        raise ValueError(
            f"Could not resolve hostname '{host}' to an IPv4 address."
        )

    if len(resolved_ips) > 1:
        print(
            f"Hostname '{host}' resolved to multiple IPv4 addresses: "
            f"{', '.join(resolved_ips)}"
        )

    ip = resolved_ips[0]

    print(f"Resolved '{raw}' -> {host} -> {ip}")

    return ip


def find_instance_id_by_ip(ec2: Any, ip: str) -> str:
    """
    Find an EC2 instance using its public or private IPv4 address.
    """

    instance_ids: list[str] = []

    # Check public IP
    response = ec2.describe_instances(
        Filters=[
            {
                "Name": "ip-address",
                "Values": [ip],
            }
        ]
    )

    for reservation in response.get("Reservations") or []:
        for instance in reservation.get("Instances") or []:
            instance_id = instance.get("InstanceId")

            if instance_id and instance_id not in instance_ids:
                instance_ids.append(instance_id)

    # Check private IP
    response = ec2.describe_instances(
        Filters=[
            {
                "Name": "private-ip-address",
                "Values": [ip],
            }
        ]
    )

    for reservation in response.get("Reservations") or []:
        for instance in reservation.get("Instances") or []:
            instance_id = instance.get("InstanceId")

            if instance_id and instance_id not in instance_ids:
                instance_ids.append(instance_id)

    if not instance_ids:
        raise RuntimeError(
            f"No EC2 instance in region has public or private IP {ip}. "
            "Check AWS_REGION and verify that the URL/IP points directly "
            "to an EC2 instance. URLs pointing to an ALB, ELB, CloudFront, "
            "or other proxy cannot be mapped directly to a single EC2 instance."
        )

    if len(instance_ids) > 1:
        raise RuntimeError(
            f"Multiple EC2 instances match IP {ip}: "
            f"{', '.join(instance_ids)}. "
            "Contact DevOps because the target IP is not unique."
        )

    instance_id = instance_ids[0]

    print(f"Matched IP {ip} to EC2 instance {instance_id}")

    return instance_id


def resolve_instance_id(
    ec2: Any,
    instance_id: str | None,
    target: str | None,
) -> str:
    """
    Resolve the final EC2 instance ID.

    If --instance-id is provided:
        Use it directly.

    If --target is provided:
        URL/hostname/IP -> IPv4 -> EC2 instance ID.
    """

    if instance_id:
        instance_id = instance_id.strip()

        validate_instance_id(instance_id)

        return instance_id

    if not target:
        raise ValueError(
            "Application URL or IP address is required."
        )

    ip = resolve_to_ipv4(target)

    return find_instance_id_by_ip(ec2, ip)


def describe_instance(
    ec2: Any,
    instance_id: str,
) -> dict[str, Any]:
    """
    Get EC2 instance details.
    """

    response = ec2.describe_instances(
        InstanceIds=[instance_id]
    )

    reservations = response.get("Reservations") or []

    if not reservations:
        raise RuntimeError(
            f"Instance {instance_id} was not found."
        )

    instances = reservations[0].get("Instances") or []

    if not instances:
        raise RuntimeError(
            f"Instance {instance_id} was not found."
        )

    return instances[0]


def instance_state_name(
    instance: dict[str, Any],
) -> str:
    """
    Get current EC2 instance state.
    """

    return instance.get(
        "State",
        {},
    ).get(
        "Name",
        "unknown",
    )


def start_instance(
    instance_id: str | None,
    target: str | None,
    region: str | None,
    dry_run: bool,
    wait_timeout: int,
) -> int:

    try:
        import boto3

        from botocore.exceptions import (
            BotoCoreError,
            ClientError,
            NoCredentialsError,
            WaiterError,
        )

    except ImportError:
        print(
            "boto3 is not installed. "
            "Run: pip install -r requirements.txt",
            file=sys.stderr,
        )

        return 2

    # Validate timeout
    if wait_timeout <= 0:
        print(
            "Wait timeout must be greater than 0 seconds.",
            file=sys.stderr,
        )

        return 2

    session_kwargs = {}

    if region:
        session_kwargs["region_name"] = region

    try:
        # ---------------------------------------------------------
        # Create AWS session
        # ---------------------------------------------------------

        session = boto3.Session(
            **session_kwargs
        )

        ec2 = session.client("ec2")

        current_region = (
            session.region_name
            or "not set"
        )

        print(
            f"Using AWS region: {current_region}"
        )

        # ---------------------------------------------------------
        # Resolve target -> EC2 instance ID
        # ---------------------------------------------------------

        resolved_id = resolve_instance_id(
            ec2=ec2,
            instance_id=instance_id,
            target=target,
        )

        print(
            f"Target resolved to instance: {resolved_id}"
        )

        # ---------------------------------------------------------
        # Get current instance details
        # ---------------------------------------------------------

        instance = describe_instance(
            ec2,
            resolved_id,
        )

        current_state = instance_state_name(
            instance
        )

        print(
            f"Instance {resolved_id} current state: "
            f"{current_state}"
        )

        # ---------------------------------------------------------
        # Already running
        # ---------------------------------------------------------

        if current_state == "running":
            print(
                "Instance is already running. "
                "No start required."
            )

            return 0

        # ---------------------------------------------------------
        # Validate state
        # ---------------------------------------------------------

        if current_state not in {
            "stopped",
            "stopping",
        }:
            print(
                f"Cannot start instance from state "
                f"'{current_state}'. EC2 can only be started "
                "from stopped (or while stopping).",
                file=sys.stderr,
            )

            return 1

        # ---------------------------------------------------------
        # Dry run
        # ---------------------------------------------------------

        if dry_run:
            print(
                "Dry run enabled."
            )

            print(
                f"Instance {resolved_id} is currently "
                f"'{current_state}'."
            )

            print(
                "No StartInstances API call will be made."
            )

            return 0

        # ---------------------------------------------------------
        # Wait if instance is stopping
        # ---------------------------------------------------------

        if current_state == "stopping":

            print(
                "Instance is currently stopping."
            )

            print(
                "Waiting until the instance becomes stopped..."
            )

            waiter = ec2.get_waiter(
                "instance_stopped"
            )

            waiter.wait(
                InstanceIds=[resolved_id],
                WaiterConfig={
                    "Delay": 15,
                    "MaxAttempts": max(
                        1,
                        wait_timeout // 15,
                    ),
                },
            )

            print(
                "Instance is now stopped."
            )

        # ---------------------------------------------------------
        # Start instance
        # ---------------------------------------------------------

        print(
            f"Starting instance {resolved_id}..."
        )

        start_response = ec2.start_instances(
            InstanceIds=[resolved_id]
        )

        starting_states = (
            start_response.get(
                "StartingInstances"
            )
            or []
        )

        if starting_states:

            previous = (
                starting_states[0]
                .get("PreviousState", {})
                .get("Name")
            )

            current = (
                starting_states[0]
                .get("CurrentState", {})
                .get("Name")
            )

            print(
                "Start requested. "
                f"Previous state: {previous}. "
                f"Current state: {current}"
            )

        # ---------------------------------------------------------
        # Wait until running
        # ---------------------------------------------------------

        print(
            f"Waiting for instance {resolved_id} "
            "to become running..."
        )

        waiter = ec2.get_waiter(
            "instance_running"
        )

        waiter.wait(
            InstanceIds=[resolved_id],
            WaiterConfig={
                "Delay": 15,
                "MaxAttempts": max(
                    1,
                    wait_timeout // 15,
                ),
            },
        )

        # ---------------------------------------------------------
        # Verify final state
        # ---------------------------------------------------------

        instance = describe_instance(
            ec2,
            resolved_id,
        )

        final_state = instance_state_name(
            instance
        )

        print(
            f"Instance {resolved_id} is now "
            f"{final_state}."
        )

        # ---------------------------------------------------------
        # Display IP information
        # ---------------------------------------------------------

        public_ip = instance.get(
            "PublicIpAddress"
        )

        private_ip = instance.get(
            "PrivateIpAddress"
        )

        if public_ip:
            print(
                f"Public IP: {public_ip}"
            )

        if private_ip:
            print(
                f"Private IP: {private_ip}"
            )

        # ---------------------------------------------------------
        # Final result
        # ---------------------------------------------------------

        if final_state == "running":
            print(
                "EC2 instance started successfully."
            )

            return 0

        print(
            "EC2 instance did not reach the running state.",
            file=sys.stderr,
        )

        return 1

    except NoCredentialsError:

        print(
            "AWS credentials were not found. "
            "Configure them using AWS_ACCESS_KEY_ID and "
            "AWS_SECRET_ACCESS_KEY, or use an IAM role "
            "on the Jenkins agent.",
            file=sys.stderr,
        )

        return 2

    except WaiterError as exc:

        print(
            "Timed out waiting for the EC2 instance "
            f"to change state: {exc}",
            file=sys.stderr,
        )

        return 1

    except ClientError as exc:

        error = exc.response.get(
            "Error",
            {},
        )

        error_code = error.get(
            "Code",
            "Unknown",
        )

        error_message = error.get(
            "Message",
            str(exc),
        )

        print(
            f"AWS API error ({error_code}): "
            f"{error_message}",
            file=sys.stderr,
        )

        return 1

    except (
        BotoCoreError,
        ValueError,
        RuntimeError,
    ) as exc:

        print(
            str(exc),
            file=sys.stderr,
        )

        return 1


def main() -> int:

    args = parse_args()

    return start_instance(
        instance_id=(
            args.instance_id.strip()
            if args.instance_id
            else None
        ),
        target=(
            args.target.strip()
            if args.target
            else None
        ),
        region=args.region,
        dry_run=args.dry_run,
        wait_timeout=args.wait_timeout,
    )


if __name__ == "__main__":
    sys.exit(main())