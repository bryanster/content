import demistomock as demisto
from CommonServerPython import *
from CommonServerUserPython import *


""" CONSTANTS """

DATE_FORMAT = "%Y-%m-%dT%H:%M:%S"  # ISO8601 format with UTC, default in XSOAR

HEADERS = {"Accept": "application/json", "Csrf-Token": "nocheck"}

ISO_8601_FORMAT = "%Y-%m-%d"

""" CLIENT CLASS """


class Client(BaseClient):
    """
    Client to use in the Exabeam DataLake integration. Overrides BaseClient
    """

    def __init__(self, base_url: str, username: str, password: str, verify: bool,
                 proxy: bool, headers):
        super().__init__(base_url=f'{base_url}', headers=headers, verify=False, proxy=proxy, timeout=20)
        self.username = username
        self.password = password

        self._login()

    def _login(self):
        """
        Logs in to the Exabeam API using the provided username and password.
        This function must be called before any other API calls.
        Note: the session is automatically closed in BaseClient's __del__
        """
        headers = {"Csrf-Token": "nocheck"}
        data = {"username": self.username, "password": self.password}

        self._http_request(
            "POST",
            full_url=f"{self._base_url}/api/auth/login",
            headers=headers,
            data=data,
        )

    def test_module_request(self):
        """
        Performs basic get request to check if the server is reachable.
        """
        self._http_request('GET', full_url=f'{self._base_url}/api/auth/check', resp_type='text')

    def query_datalake_request(self, search_query: dict) -> dict:
        return self._http_request(
            "POST",
            full_url=f"{self._base_url}/dl/api/es/search",
            data=json.dumps(search_query),
            headers={"kbn-version": "5.1.1-SNAPSHOT", "Content-Type": "application/json"},
        )


""" COMMAND FUNCTIONS """


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
        "Id": entry.get("_id"),
        "Vendor": source.get("Vendor"),
        "Created_at": source.get("@timestamp"),
        "Product": source.get("Product"),
        "Message": source.get("message")
    }


def dates_in_range(start_time, end_time):
    start_time = datetime.strptime(start_time, "%Y-%m-%d")
    end_time = datetime.strptime(end_time, "%Y-%m-%d")

    if start_time >= end_time:
        raise DemistoException("Start time must be before end time")

    if (end_time - start_time).days > 10:
        raise DemistoException("Difference between start time and end time must be less than or equal to 10 days")

    dates = []
    current_date = start_time
    while current_date <= end_time:
        dates.append(current_date.strftime("%Y.%m.%d"))
        current_date += timedelta(days=1)

    return dates


def get_date(time: str):
    date_time = arg_to_datetime(arg=time, arg_name="Start time", required=True)
    if date_time:
        date = date_time.strftime(ISO_8601_FORMAT)
    return date


def calculate_page_parameters(args: dict):
    page_arg = args.get('page')
    page_size_arg = args.get('page_size')
    limit_arg = args.get('limit')

    if (limit_arg and (page_arg or page_size_arg)) or ((not (page_arg and page_size_arg)) and (page_arg or page_size_arg)):
        raise DemistoException("You can only provide 'limit' alone or 'page' and 'page_size' together.")
    
    page = arg_to_number(args.get('page', '1'))
    page_size = arg_to_number(args.get('page_size', '50'))
    limit = arg_to_number(args.get('limit', '50'))
    
    if page and page_size:
        from_param = page * page_size - page_size
        size_param = page_size
    else:
        from_param = 0
        size_param = limit if limit is not None else 50

    return from_param, size_param


def query_datalake_command(client: Client, args: dict, cluster_name: str) -> CommandResults:
    """
    Query the datalake command and return the results in a formatted table.

    Args:
        client: The client object for interacting with the API.
        args: The arguments passed to the command.

    Returns:
        CommandResults: The command results object containing outputs and readable output.
    """
    from_param, size_param = calculate_page_parameters(args)

    if start_time := args.get("start_time", ""):
        start_time = get_date(start_time)

    if end_time := args.get("end_time", ""):
        end_time = get_date(end_time)

    dates = dates_in_range(start_time, end_time)
    dates_in_format = []
    for date in dates:
        date_exabeam = "exabeam-" + date
        dates_in_format.append(date_exabeam)

    search_query = {
        "sortBy": [
            {"field": "@timestamp", "order": "desc", "unmappedType": "date"}
        ],
        "query": args.get("query", "*"),
        "from": from_param,
        "size": size_param,
        "clusterWithIndices": [
            {
                "clusterName": cluster_name,
                "indices": dates_in_format,
            }
        ]
    }

    response = client.query_datalake_request(search_query).get("responses", [{}])

    if error := response[0].get("error", {}):
        raise DemistoException(f"Error in query: {error.get('root_cause', [{}])[0].get('reason', 'Unknown error occurred')}")

    data_response = response[0].get("hits", {}).get("hits", [])

    table_to_markdown = [_parse_entry(entry) for entry in data_response]

    return CommandResults(
        outputs_prefix="ExabeamDataLake.Event",
        outputs=data_response,
        readable_output=tableToMarkdown(name="Logs", t=table_to_markdown),
    )


def test_module(client: Client):
    """test function

    Args:
        client: Client

    Returns:
        ok if successful
    """
    client.test_module_request()
    return 'ok'


""" MAIN FUNCTION """


def main() -> None:
    params = demisto.params()
    args = demisto.args()
    command = demisto.command()

    credentials = params.get('credentials', {})
    username = credentials.get('identifier')
    password = credentials.get('password')
    base_url = params.get('url', '')
    verify_certificate = not params.get('insecure', False)
    proxy = params.get('proxy', False)
    headers = {'Accept': 'application/json', 'Csrf-Token': 'nocheck'}
    cluster_name = params.get('cluster_name', 'local')

    try:
        client = Client(
            base_url.rstrip('/'),
            verify=verify_certificate,
            username=username,
            password=password,
            proxy=proxy,
            headers=headers
        )

        demisto.debug(f"Command being called is {command}")

        match command:
            case "test-module":
                return_results(test_module(client))
            case "exabeam-data-lake-search":
                return_results(query_datalake_command(client, args, cluster_name))
            case _:
                raise NotImplementedError(f"Command {command} is not supported")

    except Exception as e:
        return_error(f"Failed to execute {command} command.\nError:\n{str(e)}")


if __name__ in ("__main__", "__builtin__", "builtins"):
    main()
