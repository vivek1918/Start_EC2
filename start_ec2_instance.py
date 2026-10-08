#!/usr/bin/env python3

"""
Start an AWS EC2 instance using an application URL, hostname, IPv4 address,
or directly using an EC2 instance ID, then wait until the application on
that instance answers its health endpoint with HTTP 200.

Success means: EC2 state is running AND GET <target>/health returns 200.
The application is expected to start on its own at boot (for example a
systemd service on Linux); this script only verifies it, so it does not
depend on the instance's operating system.

Examples:

    python start_ec2_instance.py --target https://myapp.example.com --region ap-south-1

    python start_ec2_instance.py --target myapp.example.com --region ap-south-1

    python start_ec2_instance.py --target 203.0.113.10 --region ap-south-1

    python start_ec2_instance.py --instance-id i-0123456789abcdef0 --region ap-south-1

    python start_ec2_instance.py --target 203.0.113.10 --dry-run

    python start_ec2_instance.py --target 203.0.113.10 \
        --health-port 8080 --health-timeout 600 --health-interval 15
"""

from __future__ import annotations

import argparse
import ipaddress
import re
import socket
import sys
import time
import urllib.error
import urllib.request
from typing import Any
from urllib.parse import urlparse


INSTANCE_ID_PATTERN = re.compile(r"^i-[0-9a-f]{8,17}$")

IPV4_PATTERN = re.compile(
    r"^(?:(?:25[0-5]|2[0-4]\d|[01]?\d?\d)\.){3}"
    r"(?:25[0-5]|2[0-4]\d|[01]?\d?\d)$"
)

MANAGED_SERVICE_DNS_MARKERS = (
    ("elb.amazonaws.com", "an Elastic Load Balancer (ALB/NLB/CLB)"),
    ("cloudfront.net", "a CloudFront distribution"),
    ("execute-api.", "API Gateway"),
    ("awsglobalaccelerator.com", "Global Accelerator"),
    ("awsapprunner.com", "App Runner"),
    ("amplifyapp.com", "Amplify Hosting"),
    ("s3-website", "an S3 static website"),
    ("lambda-url.", "a Lambda function URL"),
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
            "Resolve and validate the target and print the planned "
            "actions, without starting the EC2 instance or checking "
            "application health."
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

    parser.add_argument(
        "--health-path",
        default="/health",
        help="Application health endpoint path. Default: /health.",
    )

    parser.add_argument(
        "--health-port",
        type=int,
        default=None,
        help=(
            "Port for the health check. Defaults to the port in the "
            "target URL, or the scheme default (80/443)."
        ),
    )

    parser.add_argument(
        "--health-timeout",
        type=int,
        default=300,
        help=(
            "Maximum number of seconds to wait for the application "
            "health check to return HTTP 200. Default: 300."
        ),
    )

    parser.add_argument(
        "--health-interval",
        type=int,
        default=10,
        help="Seconds between health check attempts. Default: 10.",
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


def resolve_hostname(host: str) -> tuple[list[str], list[str]]:
    """
    Resolve a hostname via DNS.

    Returns (dns_names, ipv4_addresses), where dns_names contains the
    hostname, its canonical name, and any CNAME aliases.
    """

    try:
        canonical, aliases, ips = socket.gethostbyname_ex(host)

    except (socket.gaierror, socket.herror) as exc:
        raise ValueError(
            f"Could not resolve hostname '{host}' to an IPv4 address: {exc}"
        ) from exc

    names = []

    for name in [host, canonical, *aliases]:
        name = name.lower().rstrip(".")

        if name and name not in names:
            names.append(name)

    unique_ips = list(dict.fromkeys(ips))

    if not unique_ips:
        raise ValueError(
            f"Could not resolve hostname '{host}' to an IPv4 address."
        )

    return names, unique_ips


def reject_managed_service_dns(host: str, dns_names: list[str]) -> None:
    """
    Fail fast when the hostname is a CNAME to a known AWS managed service.
    """

    for name in dns_names:
        for marker, service in MANAGED_SERVICE_DNS_MARKERS:
            if marker in name:
                raise RuntimeError(
                    f"URL host '{host}' points to {service} ({name}), "
                    "not directly to an EC2 instance. Only URLs that "
                    "resolve to an EC2 instance's public IP are supported."
                )


def non_ec2_interface_reason(eni: dict[str, Any]) -> str | None:
    """
    Return why a network interface does not belong to an EC2 instance,
    or None if it is a regular interface attached to an EC2 instance.
    """

    interface_type = eni.get("InterfaceType") or "interface"
    description = eni.get("Description") or ""

    if description.startswith("ELB app/"):
        return "an Application Load Balancer"

    if (
        description.startswith("ELB net/")
        or interface_type == "network_load_balancer"
    ):
        return "a Network Load Balancer"

    if (
        description.startswith("ELB gwy/")
        or interface_type == "gateway_load_balancer"
    ):
        return "a Gateway Load Balancer"

    if description.startswith("ELB "):
        return "a Classic Load Balancer"

    if interface_type != "interface":
        return f"an AWS managed network interface of type '{interface_type}'"

    if eni.get("RequesterManaged"):
        owner = description or eni.get("RequesterId") or "unknown service"
        return f"an AWS managed network interface ({owner})"

    if not (eni.get("Attachment") or {}).get("InstanceId"):
        return "a network interface that is not attached to an EC2 instance"

    return None


def find_instance_id_by_public_ip(ec2: Any, ip: str) -> str:
    """
    Find the EC2 instance that owns a public IPv4 address, verifying
    through its network interface that the IP is not owned by a load
    balancer, NAT gateway, or other AWS managed service.
    """

    paginator = ec2.get_paginator("describe_network_interfaces")

    interfaces = [
        eni
        for page in paginator.paginate(
            Filters=[
                {
                    "Name": "association.public-ip",
                    "Values": [ip],
                }
            ]
        )
        for eni in page.get("NetworkInterfaces") or []
    ]

    if not interfaces:
        raise RuntimeError(
            f"Public IP {ip} is not associated with any network interface "
            "in this AWS account and region. It may belong to CloudFront, "
            "API Gateway, another hosting provider, or an instance in a "
            "different region or account. If the instance is stopped, "
            "note that only an Elastic IP stays attached while stopped; "
            "auto-assigned public IPs are released on stop."
        )

    eni = interfaces[0]
    eni_id = eni.get("NetworkInterfaceId", "unknown")

    reason = non_ec2_interface_reason(eni)

    if reason:
        raise RuntimeError(
            f"Public IP {ip} belongs to {reason} (network interface "
            f"{eni_id}), not an EC2 instance. Only URLs that resolve "
            "directly to an EC2 instance's public IP are supported."
        )

    instance_id = eni["Attachment"]["InstanceId"]

    print(
        f"Verified public IP {ip} belongs to EC2 instance {instance_id} "
        f"(network interface {eni_id})"
    )

    return instance_id


def find_instance_id_by_url(ec2: Any, raw: str, host: str) -> str:
    """
    URL/hostname -> DNS -> public IPv4 -> verified EC2 instance ID.
    """

    print(f"Resolving hostname: {host}")

    dns_names, ips = resolve_hostname(host)

    reject_managed_service_dns(host, dns_names)

    public_ips = [ip for ip in ips if ipaddress.ip_address(ip).is_global]

    if not public_ips:
        raise ValueError(
            f"URL host '{host}' resolves only to non-public IP(s) "
            f"{', '.join(ips)}. A URL must resolve to the EC2 instance's "
            "public IP."
        )

    print(f"Resolved '{raw}' -> {host} -> {', '.join(public_ips)}")

    instance_ids: list[str] = []

    for ip in public_ips:
        found = find_instance_id_by_public_ip(ec2, ip)

        if found not in instance_ids:
            instance_ids.append(found)

    if len(instance_ids) > 1:
        raise RuntimeError(
            f"URL host '{host}' resolves to multiple EC2 instances: "
            f"{', '.join(instance_ids)}. Use the instance's own IP or "
            "--instance-id instead."
        )

    return instance_ids[0]


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

    If --target is an IPv4 address:
        Match it against EC2 public or private IPs.

    If --target is a URL/hostname:
        Resolve it to a public IP and verify that IP belongs to an
        EC2 instance (not an ALB, NLB, CloudFront, etc.).
    """

    if instance_id:
        instance_id = instance_id.strip()

        validate_instance_id(instance_id)

        return instance_id

    if not target:
        raise ValueError(
            "Application URL or IP address is required."
        )

    host = extract_hostname(target)

    if IPV4_PATTERN.match(host):
        print(f"Target is an IPv4 address: {host}")
        return find_instance_id_by_ip(ec2, host)

    return find_instance_id_by_url(ec2, target, host)


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


def build_health_url(
    target: str | None,
    instance: dict[str, Any],
    health_path: str,
    health_port: int | None,
) -> str | None:
    """
    Build the application health URL.

    URL/IP target: keep the target's scheme, host, and port
    (http://<APP_URL_OR_IP>/health). Instance ID target: use the
    instance's public IP, falling back to its private IP.

    Returns None when there is no address to check yet.
    """

    if target:
        value = target.strip()
        parsed = urlparse(value if "://" in value else f"//{value}")
        scheme = parsed.scheme or "http"
        host = parsed.hostname
        port = health_port or parsed.port

    else:
        scheme = "http"
        host = (
            instance.get("PublicIpAddress")
            or instance.get("PrivateIpAddress")
        )
        port = health_port

    if not host:
        return None

    netloc = f"{host}:{port}" if port else host
    path = health_path if health_path.startswith("/") else f"/{health_path}"

    return f"{scheme}://{netloc}{path}"


def check_health_once(url: str, request_timeout: float) -> tuple[bool, str]:
    """
    Make a single health request. Returns (healthy, description).
    """

    try:
        with urllib.request.urlopen(url, timeout=request_timeout) as response:
            status = response.getcode()

    except urllib.error.HTTPError as exc:
        return False, f"HTTP {exc.code}"

    except urllib.error.URLError as exc:
        return False, str(exc.reason)

    except (OSError, ValueError) as exc:
        return False, str(exc) or exc.__class__.__name__

    if status == 200:
        return True, "HTTP 200"

    return False, f"HTTP {status}"


def build_health_urls(
    target: str | None,
    instance: dict[str, Any],
    health_path: str,
    health_port: int | None,
) -> list[str]:
    """
    Health URLs to try, in order.

    The first is always <APP_URL_OR_IP>/health. When APP_URL_OR_IP is a
    private IP and the instance also has a public IP, the same URL on the
    public IP is added as a fallback: the private IP is only reachable
    from inside the VPC, the public IP from outside it.
    """

    primary = build_health_url(target, instance, health_path, health_port)

    if not primary:
        return []

    parsed = urlparse(primary)
    host = parsed.hostname or ""
    public_ip = instance.get("PublicIpAddress")

    if (
        public_ip
        and host != public_ip
        and IPV4_PATTERN.match(host)
        and ipaddress.ip_address(host).is_private
    ):
        netloc = f"{public_ip}:{parsed.port}" if parsed.port else public_ip
        return [primary, parsed._replace(netloc=netloc).geturl()]

    return [primary]


def wait_for_app_health(urls: list[str], timeout: int, interval: int) -> str:
    """
    Poll the health URLs until one returns HTTP 200 or the timeout expires.

    Each attempt tries every URL in order. Returns the URL that succeeded.
    """

    print(f"Health check: {urls[0]}")

    for fallback in urls[1:]:
        print(
            f"Fallback:     {fallback} (private IP is only reachable "
            "from inside the VPC)"
        )

    print(f"Timeout: {timeout}s, retry interval: {interval}s")

    deadline = time.monotonic() + timeout
    attempt = 0

    while True:
        attempt += 1
        failures = []

        for url in urls:
            healthy, detail = check_health_once(
                url,
                request_timeout=min(interval, 5),
            )

            if healthy:
                print(f"Attempt {attempt}: {detail} from {url}")
                return url

            failures.append(
                f"{urlparse(url).hostname}: {detail}"
                if len(urls) > 1
                else detail
            )

        summary = "; ".join(failures)

        print(f"Attempt {attempt}: Application not ready ({summary})")

        remaining = deadline - time.monotonic()

        if remaining <= 0:
            raise RuntimeError(
                f"Application did not become healthy within {timeout}s. "
                f"Last result: {summary}. The EC2 instance is running, "
                "but the application is not reachable. Check that the "
                "application service is enabled at boot and that the "
                "security group allows the health check port."
            )

        time.sleep(min(interval, remaining))


def start_instance(
    instance_id: str | None,
    target: str | None,
    region: str | None,
    dry_run: bool,
    wait_timeout: int,
    health_path: str,
    health_port: int | None,
    health_timeout: int,
    health_interval: int,
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

    # Validate timeouts
    if wait_timeout <= 0 or health_timeout <= 0 or health_interval <= 0:
        print(
            "Wait timeout, health timeout, and health interval must be "
            "greater than 0 seconds.",
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

        print("Finding EC2 instance from APP_URL_OR_IP...")

        resolved_id = resolve_instance_id(
            ec2=ec2,
            instance_id=instance_id,
            target=target,
        )

        print(f"EC2 instance found: {resolved_id}")

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
        # Validate state
        # ---------------------------------------------------------

        if current_state not in {
            "running",
            "pending",
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

        needs_start = current_state in {"stopped", "stopping"}

        # ---------------------------------------------------------
        # Dry run: report the plan, change nothing, check nothing
        # ---------------------------------------------------------

        if dry_run:
            planned_url = " then ".join(
                build_health_urls(
                    target,
                    instance,
                    health_path,
                    health_port,
                )
            ) or f"http://<instance IP>{health_path}"

            print("")
            print("DRY RUN - no changes will be made. Planned actions:")

            if current_state == "stopping":
                print("  - Wait for the instance to finish stopping")

            if needs_start:
                print(f"  - Start EC2 instance {resolved_id}")
                print("  - Wait for EC2 instance to become running")
            elif current_state == "pending":
                print("  - Wait for EC2 instance to become running")
            else:
                print("  - Skip start (instance is already running)")

            print(
                f"  - Poll {planned_url} every {health_interval}s "
                f"for up to {health_timeout}s until HTTP 200"
            )
            print("")
            print(
                "Dry run complete. EC2 was not started and the "
                "application health was NOT checked."
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

        if needs_start:

            print("Starting EC2 instance...")

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

        elif current_state == "running":
            print("EC2 instance is already running. No start required.")

        # ---------------------------------------------------------
        # Wait until running
        # ---------------------------------------------------------

        if current_state != "running":

            print("Waiting for EC2 instance to become running...")

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

        if final_state != "running":
            print(
                f"EC2 instance did not reach the running state "
                f"(current state: {final_state}).",
                file=sys.stderr,
            )

            return 1

        print("EC2 instance is running.")

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
        # Wait for the application (EC2 running != app running)
        # ---------------------------------------------------------

        health_urls = build_health_urls(
            target,
            instance,
            health_path,
            health_port,
        )

        if not health_urls:
            raise RuntimeError(
                f"Instance {resolved_id} has no IP address to run the "
                "application health check against."
            )

        print("")
        print("Waiting for application to become available...")

        wait_for_app_health(
            health_urls,
            timeout=health_timeout,
            interval=health_interval,
        )

        print("")
        print("Application is healthy.")
        print("EC2 startup completed successfully.")

        return 0

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
        health_path=args.health_path,
        health_port=args.health_port,
        health_timeout=args.health_timeout,
        health_interval=args.health_interval,
    )


if __name__ == "__main__":
    sys.exit(main())