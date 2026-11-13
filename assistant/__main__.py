"""Start the assistant:  python -m assistant

Two listeners share one app:
  http://127.0.0.1:<http_port>   this PC only, trusted without pairing
  https://<lan-ip>:<https_port>  phone and other devices, PIN pairing required
(Phones only allow microphone access over HTTPS, hence the self-signed certificate.)
"""
from __future__ import annotations

import asyncio
import datetime as dt
import ipaddress
import json
import sys

import uvicorn

from .config import DATA_DIR, load_config
from .server import create_app, lan_addresses


def ensure_certificate(ips: list[str]) -> tuple[str, str]:
    """Create (or reuse) a self-signed certificate valid for this PC's LAN addresses."""
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    cert_file, key_file, meta_file = DATA_DIR / "cert.pem", DATA_DIR / "key.pem", DATA_DIR / "cert.json"
    if cert_file.exists() and key_file.exists() and meta_file.exists():
        if json.loads(meta_file.read_text()) == ips:
            return str(cert_file), str(key_file)

    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Personal Assistant")])
    now = dt.datetime.now(dt.timezone.utc)
    sans = [x509.DNSName("localhost"), x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]
    sans += [x509.IPAddress(ipaddress.ip_address(ip)) for ip in ips]
    cert = (x509.CertificateBuilder()
            .subject_name(name).issuer_name(name)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - dt.timedelta(days=1))
            .not_valid_after(now + dt.timedelta(days=825))
            .add_extension(x509.SubjectAlternativeName(sans), critical=False)
            .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
            .sign(key, hashes.SHA256()))
    cert_file.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_file.write_bytes(key.private_bytes(serialization.Encoding.PEM,
                                           serialization.PrivateFormat.PKCS8,
                                           serialization.NoEncryption()))
    meta_file.write_text(json.dumps(ips))
    return str(cert_file), str(key_file)


async def serve() -> None:
    cfg = load_config()
    app = create_app(cfg)
    ips = lan_addresses()
    cert, key = ensure_certificate(ips)

    local = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=cfg.http_port, log_level="warning"))
    remote = uvicorn.Server(uvicorn.Config(app, host="0.0.0.0", port=cfg.https_port, log_level="warning",
                                           ssl_certfile=cert, ssl_keyfile=key))
    brain = app.state.brain
    print(f"\n  {cfg.name} is running.")
    print(f"  Brain:      {'online (' + cfg.model + ')' if brain.online else 'OFFLINE - set ANTHROPIC_API_KEY'}")
    print(f"  On this PC: http://127.0.0.1:{cfg.http_port}")
    for ip in ips:
        print(f"  On phone:   https://{ip}:{cfg.https_port}   (same Wi-Fi; accept the certificate warning once)")
    print(f"  Pairing PIN: {app.state.devices.pin}")
    print("  Press Ctrl+C to stop.\n")
    await asyncio.gather(local.serve(), remote.serve())


def main() -> None:
    try:
        asyncio.run(serve())
    except KeyboardInterrupt:
        sys.exit(0)


if __name__ == "__main__":
    main()
