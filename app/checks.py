"""Проверка пути до хоста глазами клиента: DNS → TCP → TLS-рукопожатие с SNI."""
import asyncio
import ipaddress
import socket
import ssl
import time
from datetime import datetime, timezone

from cryptography import x509


def _is_ip(value: str) -> bool:
    try:
        ipaddress.ip_address(value)
        return True
    except ValueError:
        return False


async def check_target(host: str, port: int, sni: str = "") -> dict:
    res = {"dns_ok": 0, "tcp_ok": 0, "tls_ok": 0, "tls_trusted": 0,
           "tls_days": None, "latency_ms": None, "ip": None, "error": None}
    loop = asyncio.get_running_loop()
    server_name = sni or host

    try:
        infos = await asyncio.wait_for(loop.getaddrinfo(host, port, type=socket.SOCK_STREAM), 6)
        res["ip"] = infos[0][4][0]
        res["dns_ok"] = 1
    except Exception:
        res["error"] = "DNS не резолвится"
        return res

    try:
        t0 = time.monotonic()
        _, w = await asyncio.wait_for(asyncio.open_connection(res["ip"], port), 6)
        res["latency_ms"] = int((time.monotonic() - t0) * 1000)
        res["tcp_ok"] = 1
        w.close()
    except Exception:
        res["error"] = f"порт {port} закрыт или не отвечает"
        return res

    # рукопожатие без проверки — чтобы вытащить сертификат в любом случае
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    try:
        _, w = await asyncio.wait_for(
            asyncio.open_connection(res["ip"], port, ssl=ctx,
                                    server_hostname=None if _is_ip(server_name) else server_name), 8)
        der = w.get_extra_info("ssl_object").getpeercert(binary_form=True)
        w.close()
        res["tls_ok"] = 1
        if der:
            cert = x509.load_der_x509_certificate(der)
            res["tls_days"] = (cert.not_valid_after_utc - datetime.now(timezone.utc)).days
    except Exception:
        res["error"] = "TLS-рукопожатие не прошло"
        return res

    # отдельное рукопожатие с проверкой цепочки и имени
    if not _is_ip(server_name):
        try:
            _, w = await asyncio.wait_for(
                asyncio.open_connection(res["ip"], port, ssl=ssl.create_default_context(),
                                        server_hostname=server_name), 8)
            w.close()
            res["tls_trusted"] = 1
        except ssl.SSLCertVerificationError:
            res["error"] = "сертификат не доверенный или не на этот SNI"
        except Exception:
            pass
    return res
