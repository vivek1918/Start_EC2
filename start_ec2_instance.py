#!/usr/bin/env python3
"""Start an AWS EC2 instance and wait until its state is running.

Usage:
  python start_ec2_instance.py --instance-id i-0123456789abcdef0
  python start_ec2_instance.py --instance-id i-0123456789abcdef0 --region ap-south-1
  python start_ec2_instance.py --instance-id i-0123456789abcdef0 --dry-run
"""

from __future__ import annotations

import argparse
import re
import sys
from typing import Any

INSTANCE_ID_PATTERN = re.compile(r"^i-[0-9a-f]{8,17}$")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Start an EC2 instance and wait until it is running."
    )
    parser.add_argument(
        "--instance-id",
        required=True,
        help="EC2 instance ID, for example i-0123456789abcdef0",
    )
    parser.add_argument(
        "--region",
        default=None,
        help="AWS region, for example ap-south-1. Uses AWS_DEFAULT_REGION if omitted.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate arguments and credentials without starting the instance.",
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


def describe_instance(ec2: Any, instance_id: str) -> dict[str, Any]:
    response = ec2.describe_instances(InstanceIds=[instance_id])
    reservations = response.get("Reservations") or []
    if not reservations or not reservations[0].get("Instances"):
        raise RuntimeError(f"Instance {instance_id} was not found.")
    return reservations[0]["Instances"][0]


def instance_state_name(instance: dict[str, Any]) -> str:
    return instance.get("State", {}).get("Name", "unknown")


def start_instance(instance_id: str, region: str | None, dry_run: bool, wait_timeout: int) -> int:
    try:
        import boto3
        from botocore.exceptions import BotoCoreError, ClientError, NoCredentialsError, WaiterError
    except ImportError:
        print("boto3 is not installed. Run: pip install -r requirements.txt", file=sys.stderr)
        return 2

    validate_instance_id(instance_id)
    session_kwargs = {}
    if region:
        session_kwargs["region_name"] = region

    try:
        session = boto3.Session(**session_kwargs)
        ec2 = session.client("ec2")
        current_region = session.region_name or "not set"
        print(f"Using AWS region: {current_region}")

        instance = describe_instance(ec2, instance_id)
        current_state = instance_state_name(instance)
        print(f"Instance {instance_id} current state: {current_state}")

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
                InstanceIds=[instance_id],
                WaiterConfig={"Delay": 15, "MaxAttempts": max(1, wait_timeout // 15)},
            )

        print(f"Starting instance {instance_id}...")
        start_response = ec2.start_instances(InstanceIds=[instance_id])
        starting_states = start_response.get("StartingInstances") or []
        if starting_states:
            previous = starting_states[0].get("PreviousState", {}).get("Name")
            current = starting_states[0].get("CurrentState", {}).get("Name")
            print(f"Start requested. Previous state: {previous}. Current state: {current}")

        waiter = ec2.get_waiter("instance_running")
        waiter.wait(
            InstanceIds=[instance_id],
            WaiterConfig={"Delay": 15, "MaxAttempts": max(1, wait_timeout // 15)},
        )

        instance = describe_instance(ec2, instance_id)
        final_state = instance_state_name(instance)
        print(f"Instance {instance_id} is now {final_state}.")
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
        instance_id=args.instance_id.strip(),
        region=args.region,
        dry_run=args.dry_run,
        wait_timeout=args.wait_timeout,
    )


if __name__ == "__main__":
    sys.exit(main())
