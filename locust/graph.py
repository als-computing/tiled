import logging
import os
import random

import requests

from locust import HttpUser, between, events, task

logger = logging.getLogger(__name__)


@events.init_command_line_parser.add_listener
def _(parser):
    parser.add_argument(
        "--api-key",
        type=str,
        default="secret",
        help="API key for Tiled authentication (default: secret)",
    )
    parser.add_argument(
        "--entity-count",
        type=int,
        default=20,
        help="Number of entities to pre-seed for read tasks (default: 20)",
    )


@events.init.add_listener
def on_locust_init(environment, **kwargs):
    if environment.host is None:
        raise ValueError(
            "Host must be specified with --host argument, or through the web-ui."
        )

    environment.entity_ids, environment.link_ids = create_test_graph(
        environment.host,
        environment.parsed_options.api_key,
        environment.parsed_options.entity_count,
    )


def create_test_graph(host, api_key, entity_count):
    """Seed a chain of entities, linked head-to-tail, for read tasks to query."""
    headers = {"Authorization": f"Apikey {api_key}"}

    entity_ids = []
    for i in range(entity_count):
        result = _post_graphql(
            host,
            headers,
            MUTATION_CREATE_ENTITY,
            {
                "input": {
                    "entityType": "locust-sample",
                    "name": f"locust-entity-{i}",
                    "properties": {"i": i},
                }
            },
        )
        entity_ids.append(result["data"]["createEntity"]["id"])

    link_ids = []
    for subject_id, object_id in zip(entity_ids, entity_ids[1:]):
        result = _post_graphql(
            host,
            headers,
            MUTATION_CREATE_LINK,
            {
                "input": {
                    "subjectId": subject_id,
                    "predicate": "relates_to",
                    "objectId": object_id,
                }
            },
        )
        link_ids.append(result["data"]["createLink"]["id"])

    logger.info(
        f"Seeded {len(entity_ids)} entities and {len(link_ids)} links for graph load test"
    )
    return entity_ids, link_ids


def _post_graphql(host, headers, query, variables):
    response = requests.post(
        f"{host}/api/graphql",
        json={"query": query, "variables": variables},
        headers=headers,
    )
    response.raise_for_status()
    payload = response.json()
    if payload.get("errors"):
        raise RuntimeError(f"GraphQL errors during setup: {payload['errors']}")
    return payload


QUERY_ENTITIES = """
query Entities($limit: Int!) {
  entities(limit: $limit) {
    id
    name
    entityType
    uri
  }
}
"""

QUERY_ENTITY_BY_ID = """
query Entity($id: ID!) {
  entity(id: $id) {
    id
    name
    entityType
    createdAt
    outgoingLinks(limit: 5) {
      id
      predicate
      object {
        id
        name
      }
    }
    incomingLinks(limit: 5) {
      id
      predicate
      subject {
        id
        name
      }
    }
  }
}
"""

QUERY_LINKS = """
query Links($limit: Int!) {
  links(limit: $limit) {
    id
    predicate
    subjectId
    objectId
  }
}
"""

QUERY_LINK_BY_ID = """
query Link($id: ID!) {
  link(id: $id) {
    id
    predicate
    subject {
      id
      name
    }
    object {
      id
      name
    }
  }
}
"""

QUERY_NAMESPACES = """
query Namespaces {
  namespaces {
    prefix
    uri
  }
}
"""

# Traverses two hops deep (well within the server's QueryDepthLimiter), to
# exercise the recursive Entity -> outgoingLinks -> Entity resolution path.
QUERY_TRAVERSAL = """
query Traversal($id: ID!) {
  entity(id: $id) {
    id
    name
    outgoingLinks(limit: 3) {
      predicate
      object {
        id
        name
        outgoingLinks(limit: 3) {
          predicate
          object {
            id
            name
          }
        }
      }
    }
  }
}
"""

MUTATION_CREATE_ENTITY = """
mutation CreateEntity($input: CreateEntityInput!) {
  createEntity(input: $input) {
    id
    name
  }
}
"""

MUTATION_CREATE_LINK = """
mutation CreateLink($input: CreateLinkInput!) {
  createLink(input: $input) {
    id
    predicate
  }
}
"""


def _check_graphql_response(response):
    """Mark a locust response as a failure on transport OR GraphQL-level errors."""
    if response.status_code != 200:
        response.failure(f"HTTP {response.status_code}")
        return
    try:
        payload = response.json()
    except ValueError:
        response.failure("invalid JSON response")
        return
    if payload.get("errors"):
        response.failure(str(payload["errors"]))


class GraphQLUser(HttpUser):
    """User that reads and writes the entity/link graph over /api/graphql."""

    wait_time = between(0.5, 2)

    def on_start(self):
        self.client.headers = {
            "Authorization": f"Apikey {self.environment.parsed_options.api_key}"
        }
        self.entity_ids = self.environment.entity_ids
        self.link_ids = self.environment.link_ids
        self.write_count = 0

    def _post(self, name, query, variables):
        with self.client.post(
            "/api/graphql",
            json={"query": query, "variables": variables},
            name=f"/api/graphql [{name}]",
            catch_response=True,
        ) as response:
            _check_graphql_response(response)

    @task(3)
    def query_entities(self):
        self._post("entities", QUERY_ENTITIES, {"limit": 20})

    @task(3)
    def query_entity_by_id(self):
        self._post(
            "entity",
            QUERY_ENTITY_BY_ID,
            {"id": random.choice(self.entity_ids)},
        )

    @task(2)
    def query_links(self):
        self._post("links", QUERY_LINKS, {"limit": 20})

    @task(2)
    def query_link_by_id(self):
        self._post("link", QUERY_LINK_BY_ID, {"id": random.choice(self.link_ids)})

    @task(1)
    def query_namespaces(self):
        self._post("namespaces", QUERY_NAMESPACES, {})

    @task(2)
    def query_traversal(self):
        self._post(
            "traversal",
            QUERY_TRAVERSAL,
            {"id": random.choice(self.entity_ids)},
        )

    @task(1)
    def mutation_create_entity(self):
        self.write_count += 1
        self._post(
            "createEntity",
            MUTATION_CREATE_ENTITY,
            {
                "input": {
                    "entityType": "locust-sample",
                    "name": f"locust-write-{os.getpid()}-{self.write_count}",
                    "properties": {},
                }
            },
        )

    @task(1)
    def mutation_create_link(self):
        subject_id, object_id = random.sample(self.entity_ids, 2)
        self._post(
            "createLink",
            MUTATION_CREATE_LINK,
            {
                "input": {
                    "subjectId": subject_id,
                    "predicate": "locust_relates_to",
                    "objectId": object_id,
                }
            },
        )
