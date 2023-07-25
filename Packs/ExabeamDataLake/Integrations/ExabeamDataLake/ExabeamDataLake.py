import demistomock as demisto
from CommonServerPython import *  # noqa
from CommonServerUserPython import *  # noqa


""" CONSTANTS """

DATE_FORMAT = "%Y-%m-%dT%H:%M:%SZ"  # ISO8601 format with UTC, default in XSOAR
HEADERS = {"Accept": "application/json", "Csrf-Token": "nocheck"}

""" CLIENT CLASS """


class Client(BaseClient):
    """
    Client to use in the Exabeam DataLake integration. Overrides BaseClient
    """

    def __init__(
        self,
        base_url: str,
        username: str,
        password: str,
        verify: bool,
        proxy: bool,
    ):
        super().__init__(
            base_url=f"{base_url}", headers=HEADERS, verify=verify, proxy=proxy
        )
        self.username = username
        self.password = password

        if not proxy:
            self._session.trust_env = False  # TODO - need to check what this does

        self._login()

    def _login(self):
        """
        Login using the credentials and store the cookie in the session(in the BaseClient class).
        """
        self._http_request(
            "POST",
            full_url=f"{self._base_url}/api/auth/login",
            data={"username": self.username, "password": self.password},
        )

    def test_module_request(self):
        """
        Performs basic get request to check if the server is reachable.
        """
        self._http_request(
            "GET", full_url=f"{self._base_url}/api/auth/check", resp_type="text"
        )

    def query_datalake_request(self, search_query: dict) -> dict:
        headers = {"kbn-version": "5.1.1-SNAPSHOT", "Content-Type": "application/json"}
        return self._http_request(
            "POST",
            full_url=f"{self._base_url}/dl/api/es/search",
            data=json.dumps(search_query),
            headers=headers,
        )


""" COMMAND FUNCTIONS """


def _handle_time_range_query(start_time: int, end_time: int | None) -> dict:
    """Handle time range query
     Args:
          start_time: start time
          end_time: end time
    Returns:
        dict: time range query
    """

    if end_time and (start_time > end_time):
        raise DemistoException("Start time must be before end time")

    query_range: dict = {
        "rangeQuery": {
            "field": "@timestamp",
            "gte": str(start_time),
        }
    }
    if end_time:
        query_range["rangeQuery"].update({"lte": str(end_time)})

    return query_range


def query_datalake_command(client: Client, args: dict) -> CommandResults:
    """
    Query the datalake command and return the results in a formatted table.

    Args:
        client: The client object for interacting with the API.
        args: The arguments passed to the command.

    Returns:
        CommandResults: The command results object containing outputs and readable output.
    """

    def _parse_entry(entry: dict) -> dict:
        """
        Parse a single entry from the API response to a dictionary.

        Args:
            entry: The entry from the API response.

        Returns:
            dict: The parsed entry dictionary.
        """
        source: dict = entry.get("_source", {})
        return {
            "id": entry.get("_id"),
            "Vendor": source.get("Vendor"),
            "time": source.get("time"),
            "Product": source.get("Product"),
            "event name": source.get("event_name"),
            "action": source.get("action"),
        }

    query = args["query"]
    limit = arg_to_number(args.get("limit", 50))
    all_result = argToBoolean(args.get("all_result", False))

    result_size_to_get = 10_000 if all_result else limit

    if start_time := args.get("start_time"):
        start_time = date_to_timestamp(start_time)

    if end_time := args.get("end_time"):
        end_time = date_to_timestamp(end_time)

    search_query = _handle_time_range_query(start_time, end_time) if start_time else {}

    search_query.update(
        {
            "sortBy": [
                {"field": "@timestamp", "order": "desc", "unmappedType": "date"}
            ],  # the response sort by timestamp
            "query": query,  # can be "VPN" or "*"
            "size": result_size_to_get,  # the size of the response
            "clusterWithIndices": [
                {
                    "clusterName": "local",
                    "indices": ["exabeam-2023.07.12"],
                }  # TODO -need to check if this is hardcoded
            ],
        }
    )

    response = client.query_datalake_request(search_query)
    if error := response["responses"][0].get("error"):
        raise DemistoException(f"Error in query: {error['root_cause'][0]['reason']}")

    data_response = response["responses"][0]["hits"]["hits"]
    table_to_markdown = [_parse_entry(entry) for entry in data_response]
    markdown_table = tableToMarkdown(name="Logs", t=table_to_markdown)

    return CommandResults(
        outputs_prefix="ExabeamDataLake.Log",
        outputs=data_response,
        readable_output=markdown_table,
    )


def test_module(client: Client):
    """test function

    Args:
        client: Client

    Returns:
        ok if successful
    """
    client.test_module_request()
    demisto.results("ok")


""" MAIN FUNCTION """


def main() -> None:
    params = demisto.params()
    args = demisto.args()
    command = demisto.command()

    username = params["credentials"]["identifier"]
    password = params["credentials"]["password"]
    base_url = params["url"].rstrip("/")

    verify_certificate = not params.get("insecure", False)

    proxy = params.get("proxy", False)

    try:
        client = Client(
            base_url,
            verify=verify_certificate,
            username=username,
            password=password,
            proxy=proxy,
        )

        demisto.debug(f"Command being called is {command}")

        match command:
            case "test-module":
                return_results(test_module(client))
            case "exabeam-data-lake-query":
                return_results(query_datalake_command(client, args))
            case _:
                raise NotImplementedError(f"Command {command} is not supported")

    except Exception as e:
        return_error(f"Failed to execute {command} command.\nError:\n{str(e)}")


if __name__ in ("__main__", "__builtin__", "builtins"):
    main()
