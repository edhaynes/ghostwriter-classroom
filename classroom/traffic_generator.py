"""Network traffic generator for intrusion detection mode.

Generates synthetic network traffic datasets containing:
- Legitimate traffic (70%)
- Red herrings (20%) - SSH failed logins, isolated suspicious events
- Actual attack (10%) - Multi-port probe with same MAC, rotating IPs
"""

import random
from datetime import datetime, timedelta, timezone

from .models import NetworkTraffic, NetworkTrafficEntry, ModelConfig


def _random_mac() -> str:
    """Generate a random MAC address."""
    return ":".join([f"{random.randint(0, 255):02x}" for _ in range(6)])


def _generate_legitimate_traffic(start: datetime, count: int) -> list[NetworkTrafficEntry]:
    """Generate normal traffic: web browsing, DNS, email, etc."""
    entries = []
    legitimate_ports = [80, 443, 53, 25, 110, 143, 993, 995]

    for i in range(count):
        entries.append(NetworkTrafficEntry(
            timestamp=start + timedelta(seconds=random.randint(0, 3600)),
            source_ip=f"192.168.1.{random.randint(10, 200)}",
            source_mac=_random_mac(),
            dest_ip=f"{random.randint(1, 223)}.{random.randint(0, 255)}.{random.randint(0, 255)}.{random.randint(1, 254)}",
            dest_port=random.choice(legitimate_ports),
            protocol=random.choice(["TCP", "UDP"]),
            event_type="connection",
            details="Normal traffic"
        ))
    return entries


def _generate_red_herrings(start: datetime, count: int) -> list[NetworkTrafficEntry]:
    """Generate red herrings: SSH failed logins, isolated port scans, etc."""
    entries = []

    for i in range(count):
        herring_type = random.choice(["ssh_fail", "isolated_scan", "ping_sweep"])

        if herring_type == "ssh_fail":
            # SSH failed login attempts (different MACs, random times)
            entries.append(NetworkTrafficEntry(
                timestamp=start + timedelta(seconds=random.randint(0, 3600)),
                source_ip=f"203.0.113.{random.randint(1, 254)}",  # External IP
                source_mac=_random_mac(),  # Different MAC each time
                dest_ip="192.168.1.100",
                dest_port=22,
                protocol="TCP",
                event_type="failed_login",
                details=f"Failed SSH login attempt"
            ))
        elif herring_type == "isolated_scan":
            # Random isolated port scan (not part of pattern)
            entries.append(NetworkTrafficEntry(
                timestamp=start + timedelta(seconds=random.randint(0, 3600)),
                source_ip=f"198.51.100.{random.randint(1, 254)}",
                source_mac=_random_mac(),
                dest_ip=f"192.168.1.{random.randint(1, 254)}",
                dest_port=random.choice([21, 23, 25, 139, 445, 3306, 5432, 8080]),
                protocol="TCP",
                event_type="connection",
                details="Isolated connection attempt"
            ))
        else:  # ping_sweep
            # ICMP ping sweep (different target)
            entries.append(NetworkTrafficEntry(
                timestamp=start + timedelta(seconds=random.randint(0, 3600)),
                source_ip=f"10.0.0.{random.randint(1, 254)}",
                source_mac=_random_mac(),
                dest_ip=f"192.168.1.{random.randint(50, 150)}",
                dest_port=0,  # ICMP has no port
                protocol="ICMP",
                event_type="ping",
                details="ICMP echo request"
            ))

    return entries


def _generate_port_scan_attack(
    start: datetime,
    pattern: dict,
    count: int
) -> list[NetworkTrafficEntry]:
    """Generate the actual attack: multi-port probe with same MAC, rotating IPs."""
    entries = []
    time_window = 15 * 60  # 15 minutes in seconds

    for i in range(count):
        entries.append(NetworkTrafficEntry(
            timestamp=start + timedelta(seconds=random.randint(0, time_window)),
            source_ip=random.choice(pattern["spoofed_ips"]),  # Rotating IPs
            source_mac=pattern["attacker_mac"],  # SAME MAC - key indicator!
            dest_ip=pattern["target_ip"],
            dest_port=random.choice(pattern["target_ports"]),
            protocol="TCP",
            event_type="port_scan",
            details="SYN packet, no response"
        ))

    return entries


async def generate_intrusion_traffic(
    config: ModelConfig,
    difficulty: str = "medium"
) -> NetworkTraffic:
    """Generate synthetic network traffic with embedded attack.

    Args:
        config: Model configuration (unused, reserved for future AI-enhanced generation)
        difficulty: "easy", "medium", or "hard"

    Returns:
        NetworkTraffic with legitimate traffic, red herrings, and attack pattern
    """
    # Define the attack pattern (consistent across difficulty levels)
    attack_pattern = {
        "attacker_mac": "00:1a:2b:3c:4d:5e",
        "target_ip": "192.168.1.100",
        "spoofed_ips": [
            "10.0.1.15", "10.0.1.23", "10.0.1.67",
            "10.0.1.89", "10.0.1.134", "10.0.1.201"
        ],
        "target_ports": [22, 80, 443, 3389, 8080, 3306, 5432, 8443],
        "duration_minutes": 15
    }

    # Adjust traffic volume based on difficulty
    if difficulty == "easy":
        legitimate_count = 100
        red_herring_count = 20
        attack_count = 30  # More attack events = easier to spot
    elif difficulty == "hard":
        legitimate_count = 180
        red_herring_count = 60
        attack_count = 15  # Fewer attack events = harder to spot
    else:  # medium (default)
        legitimate_count = 140
        red_herring_count = 40
        attack_count = 20

    entries = []
    start_time = datetime.now(timezone.utc) - timedelta(hours=1)

    # Generate traffic
    entries.extend(_generate_legitimate_traffic(start_time, legitimate_count))
    entries.extend(_generate_red_herrings(start_time, red_herring_count))

    # Attack starts 20 minutes into the hour
    attack_start = start_time + timedelta(minutes=20)
    entries.extend(_generate_port_scan_attack(attack_start, attack_pattern, attack_count))

    # Shuffle and sort by timestamp for realism
    random.shuffle(entries)
    entries.sort(key=lambda e: e.timestamp)

    return NetworkTraffic(
        entries=entries,
        attack_description=(
            f"Multi-port probe targeting {attack_pattern['target_ip']} "
            f"over 15 minutes using MAC address {attack_pattern['attacker_mac']} "
            f"with rotating source IPs ({', '.join(attack_pattern['spoofed_ips'][:3])}, etc.). "
            f"Probed ports: {', '.join(map(str, attack_pattern['target_ports'][:5]))}"
        ),
        metadata={
            "difficulty": difficulty,
            "attack_type": "port_scan",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "attacker_mac": attack_pattern["attacker_mac"],
            "target_ip": attack_pattern["target_ip"]
        }
    )
