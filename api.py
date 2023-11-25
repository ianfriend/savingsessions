import logging
import requests
from datetime import datetime, timezone
from dataclasses import dataclass
from rich import print


def parse_timestamp(s):
    # 2021-11-12 03:30:00+00:00
    s = s.replace(" ", "T")
    return datetime.strptime(s, "%Y-%m-%dT%H:%M:%S%z").astimezone(timezone.utc)


@dataclass
class Account:
    number: str


@dataclass
class ElectricityMeter:
    id: str
    serialNumber: str


@dataclass
class ElectricityMeterPoint:
    id: str
    mpan: str
    meters: list[ElectricityMeter]

    def __post_init__(self):
        self.meters = [ElectricityMeter(**m) for m in self.meters]


@dataclass
class Tariff:
    productCode: str


@dataclass
class Reading:
    startAt: datetime
    endAt: datetime
    value: float

    def __post_init__(self):
        self.startAt = parse_timestamp(self.startAt)
        self.endAt = parse_timestamp(self.endAt)
        self.value = float(self.value)


@dataclass
class Agreement:
    id: int
    validFrom: datetime
    validTo: datetime | None
    tariff: Tariff
    meterPoint: ElectricityMeterPoint

    def __post_init__(self):
        self.validFrom = parse_timestamp(self.validFrom)
        self.validTo = parse_timestamp(self.validTo) if self.validTo else None
        self.tariff = Tariff(**self.tariff)
        self.meterPoint = ElectricityMeterPoint(**self.meterPoint)


@dataclass
class EnergyProduct:
    fullName: str
    direction: str


class APIError(Exception):
    pass


class AuthenticationError(APIError):
    pass


class API:
    base_url = "https://api.octopus.energy/v1/graphql/"
    logger = logging.getLogger("graphql")

    def __init__(self, api_key: str):
        self.api_key = api_key
        self.token = None

    def _request(self, query, **variables):
        headers = {}
        if self.token:
            headers["Authorization"] = self.token
        self.logger.debug("request: %s variables: %r", query, variables)
        resp = requests.post(
            self.base_url,
            json={"query": query, "variables": variables},
            headers=headers,
        )
        if not resp.ok:
            self.logger.error("error: %s", resp.text)
            resp.raise_for_status()
        self.logger.debug("response: %s", resp.text)
        if errors := resp.json().get("errors"):
            if any(
                "extensions" in error
                and error["extensions"]["errorCode"] == "KT-CT-1139"
                for error in errors
            ):
                raise AuthenticationError(errors[0]["extensions"]["errorDescription"])
            raise APIError(errors)
        return resp.json()["data"]

    def authenticate(self):
        query = """mutation krakenTokenAuthentication($key: String!) {
  obtainKrakenToken(input: {APIKey: $key}) {
    token
  }
}"""
        data = self._request(query, key=self.api_key)
        print(data)
        self.token = data["obtainKrakenToken"]["token"]

    def accounts(self):
        query = """query accounts {
  viewer {
    accounts {
      number
    }
  }
}"""
        data = self._request(query)
        return [Account(**row) for row in data["viewer"]["accounts"]]

    def agreements(self, account: str):
        query = """query agreements($account: String!) {
  account(accountNumber: $account) {
    electricityAgreements {
      id
      validFrom
      validTo
      tariff {
        ... on TariffType {
          productCode
        }
      }
      meterPoint {
        id
        mpan
        meters {
            id
            serialNumber
        }
      }
    }
  }
}"""
        data = self._request(query, account=account)
        agreements = [Agreement(**a) for a in data["account"]["electricityAgreements"]]
        return agreements

    def energy_product(self, code: str):
        query = """query product($code: String!) {
  energyProduct(code:$code) {
    direction
    fullName
  }
}
        """
        data = self._request(query, code=code)
        return EnergyProduct(**data["energyProduct"])

    def half_hourly_readings(
        self, mpan: str, meter: str, start_at: datetime, first: int
    ):
        query = """query halfHourlyReadings($mpan: ID, $meter: Int, $startAt: DateTime!, $first: Int) {
  meterPoints(mpan: $mpan) {
    meters(id: $meter) {
      consumption(startAt: $startAt, grouping: HALF_HOUR, timezone: "UTC", first: $first) {
        edges {
          node {
            value
            startAt
            endAt
          }
        }
      }
    }
  }
}
        """
        data = self._request(
            query,
            mpan=mpan,
            meter=int(meter),
            startAt=start_at.strftime("%Y-%m-%dT%H:%M:%S%z"),
            first=first,
        )
        edges = data["meterPoints"]["meters"][0]["consumption"]["edges"]
        readings = [Reading(**edge["node"]) for edge in edges]
        return readings
