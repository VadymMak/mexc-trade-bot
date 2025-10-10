import time
import argparse
import os
import sys
from prometheus_client import start_http_server
# Ensure project root is importable when running from /scripts
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app.infra import metrics as m  # <- your file


def touch_metrics():
    m.ticks_total.labels("BTCUSDT").inc()
    m.ws_lag_seconds.labels("ETHUSDT").observe(0.12)
    m.api_scan_requests_total.inc()
    m.api_scan_latency_seconds.observe(0.03)
    m.ws_lag_ms.set(850)
    m.scanner_cache_hitrate.set(0.73)


def main():
    parser = argparse.ArgumentParser(description="Prometheus metrics smoke test")
    parser.add_argument("--port", type=int, default=9000, help="Exporter port")
    parser.add_argument("--iterations", type=int, default=10, help="How many touches before exit")
    parser.add_argument("--sleep", type=float, default=2.0, help="Seconds between touches")
    args = parser.parse_args()

    start_http_server(args.port)
    print(f"metrics exporter listening on http://127.0.0.1:{args.port}/metrics")
    try:
        for i in range(args.iterations):
            touch_metrics()
            time.sleep(args.sleep)
        print("✓ smoke test finished")
    except KeyboardInterrupt:
        print("\nInterrupted by user, exiting gracefully.")


if __name__ == "__main__":
    main()
