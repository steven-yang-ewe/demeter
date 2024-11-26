from google.analytics.data_v1beta import BetaAnalyticsDataClient
from google.analytics.data_v1beta.types import (
    DateRange,
    Dimension,
    Metric,
    RunReportRequest,
)

import os

# Set an environment variable
os.environ['GOOGLE_APPLICATION_CREDENTIALS'] = '/Users/lunaspeed/Documents/ewe/ewe-remixdao-prod-c686f82f7852.json'


def sample_run_report(property_id="YOUR-GA4-PROPERTY-ID"):
    """Runs a simple report on a Google Analytics 4 property."""
    # TODO(developer): Uncomment this variable and replace with your
    #  Google Analytics 4 property ID before running the sample.
    # property_id = "YOUR-GA4-PROPERTY-ID"

    # Using a default constructor instructs the client to use the credentials
    # specified in GOOGLE_APPLICATION_CREDENTIALS environment variable.
    client = BetaAnalyticsDataClient()

    request = RunReportRequest(
        property=f"properties/{property_id}",
        dimensions=[Dimension(name="date")],
        metrics=[Metric(name="activeUsers")],
        date_ranges=[DateRange(start_date="2024-11-01", end_date="today")],
    )
    response = client.run_report(request)

    print("Report result:")
    print(response)
    # for row in response.rows:
    #     print(row.dimension_values[0].value, row.metric_values[0].value)


if __name__ == "__main__":
    sample_run_report('447532399')
