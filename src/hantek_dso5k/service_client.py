"""Small local client for the persistent Hantek service."""

import argparse
import json
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class Client:
    def __init__(self, config):
        settings = json.loads(Path(config).read_bytes())
        endpoint = urlsplit(settings["endpoint"])
        if (endpoint.scheme != "http" or endpoint.hostname != "127.0.0.1" or endpoint.port is None
                or endpoint.netloc != f"127.0.0.1:{endpoint.port}" or endpoint.path or endpoint.query
                or endpoint.fragment):
            raise ValueError("Client configuration must identify the local IPv4 loopback service")
        self.endpoint = settings["endpoint"]
        self.headers = {"Authorization": "Bearer " + settings["token"], "Content-Type": "application/json"}
        self.opener = build_opener(ProxyHandler({}), NoRedirect())

    def request(self, route, payload=None):
        request = Request(self.endpoint + "/v1/" + route,
                          data=None if payload is None else json.dumps(payload).encode(),
                          headers=self.headers)
        with self.opener.open(request, timeout=10) as response:
            return response.read()

    def json(self, route, payload=None):
        return json.loads(self.request(route, payload))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("config", type=Path)
    parser.add_argument("operation", choices=("status", "settings", "measurements", "capabilities",
                                              "snapshot", "triggered", "run", "stop", "s22-probe",
                                              "job", "artifact", "shutdown"))
    parser.add_argument("--path", choices=("auto", "native", "frozen"), default="auto")
    parser.add_argument("--allow-overwrite", action="store_true")
    parser.add_argument("--configure-fixture", action="store_true", help="Explicit S22 comparison with verified fixture edits")
    parser.add_argument("--job-id")
    parser.add_argument("--artifact")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    operation = args.operation
    try:
        client = Client(args.config)
        if operation in ("snapshot", "triggered", "s22-probe"):
            payload = {"kind": operation}
            if operation == "triggered":
                payload.update(path=args.path, allow_overwrite=args.allow_overwrite)
            elif operation == "s22-probe":
                payload["configure_fixture"] = args.configure_fixture
            result = client.json("jobs", payload)
        elif operation in ("run", "stop"):
            result = client.json("jobs", {"kind": "run-state", "running": operation == "run",
                                          "allow_overwrite": args.allow_overwrite})
        elif operation == "shutdown":
            result = client.json(operation, {})
        elif operation in ("job", "artifact"):
            if not args.job_id:
                parser.error("--job-id is required")
            route = "jobs/" + args.job_id
            if operation == "artifact":
                if not args.artifact or args.output is None:
                    parser.error("--artifact and --output are required")
                data = client.request(route + "/artifacts/" + args.artifact)
                with args.output.open("xb") as stream:
                    stream.write(data)
                return 0
            result = client.json(route)
        else:
            result = client.json(operation)
    except (HTTPError, URLError, OSError, ValueError) as exc:
        parser.exit(1, str(exc) + "\n")
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
