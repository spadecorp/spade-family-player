"""Measure a first viewing against an operator-specified analysis service."""
import argparse
import getpass
import json
import os
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/"client"))
from session import Session, endpoint


def main():
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("movie", type=Path)
    cli.add_argument("--service", required=True)
    cli.add_argument("--allow-upload", action="store_true", required=True,
                     help="Send the movie's sampled images and audio to this service.")
    cli.add_argument("--fast", action="store_true", help="Advance immediately through ready sections.")
    args = cli.parse_args()
    endpoint(args.service)
    token = os.environ.get("SPADE_TEST_TOKEN") or getpass.getpass("Service access token: ")
    with tempfile.TemporaryDirectory() as directory:
        cache = Path(directory)/"benchmark.sqlite"
        started = time.monotonic()
        s = Session(args.movie, cache)
        try:
            s.grant(args.service,token)
            index, stalls, first, stall_time = 0, 0, None, 0.0
            while True:
                wait_start = time.monotonic()
                while True:
                    state = s.state()
                    if state["phase"] == "error":
                        raise RuntimeError(state["message"])
                    if state["part"] and state["part"]["index"] == index:
                        break
                    time.sleep(.05)
                waited = time.monotonic()-wait_start
                if first is None:
                    first = time.monotonic()-started
                elif not args.fast and waited > .10:
                    stalls += 1
                    stall_time += waited
                part = state["part"]
                if not args.fast:
                    time.sleep(sum(b-a for a,b in part["keep"]))
                index += 1
                if index == state["count"]:
                    break
                s.seek(index)
            print(json.dumps({
                "mode": "unpaced" if args.fast else "paced at filtered playback duration",
                "movie_seconds": s.duration, "sections": s.count,
                "seconds_to_first_filtered_file": round(first,2),
                "seconds_total": round(time.monotonic()-started,2),
                "waits_over_100ms": None if args.fast else stalls,
                "total_wait_seconds": None if args.fast else round(stall_time,2),
                "successful_request_body_bytes": s.upload_bytes,
                "server_decode_and_inference_seconds": round(s.worker_seconds,2),
                "timestamp_database_bytes":cache.stat().st_size,
            }, indent=2))
        finally:
            s.close()
            s.worker.join()


if __name__ == "__main__":
    main()
