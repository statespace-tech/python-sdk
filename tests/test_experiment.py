from statespace import Client

from .conftest import IDENTITY_SHA256, Service

US = {"country": "US"}
TREATED = "u_1"  # bucket 0.18, inside [0, 0.5)
CONTROL = "u_2"  # bucket 0.65


def publish_ranking(service: Service, status: str = "running") -> None:
    service.publish(
        "ranking",
        status,
        bm25=(
            [[0.0, 0.5]],
            {
                "top_k": {"kind": "value", "value": 50},
                "label": {"kind": "value", "value": "bm25"},
                "ranker": {"kind": "function", "sha256": IDENTITY_SHA256},
            },
        ),
    )


def test_groups_return_their_values_and_control_returns_defaults(
    service: Service, client: Client
) -> None:
    publish_ranking(service)
    experiment = client.experiment("ranking")

    treated = experiment.assign(TREATED, context=US)
    assert treated.name == "bm25"
    assert treated.value("top_k", 10) == 50
    assert treated.value("missing", "default") == "default"
    assert treated.function("ranker", lambda items: [])([3, 1, 2]) == [3, 1, 2]

    control = experiment.assign(CONTROL, context=US)
    assert control.name == "control"
    assert control.value("top_k", 10) == 10
    assert control.function("ranker", sorted)([3, 1, 2]) == [1, 2, 3]

    assert client.flush()
    assert [(run["subject_id"], run["group"]) for run in service.runs] == [
        (TREATED, "bm25"),
        (CONTROL, "control"),
    ]


def test_wrong_types_fall_back_and_are_recorded(service: Service, client: Client) -> None:
    publish_ranking(service)
    group = client.experiment("ranking").assign(TREATED, context=US)
    assert group.value("top_k", "ten") == "ten"
    assert group.value("label", 1.5) == 1.5
    assert group.function("top_k", len)([1]) == 1
    assert group.value("ranker", 0) == 0
    assert client.flush()
    errors = [outcome["data"] for outcome in service.outcomes]
    assert [error["parameter"] for error in errors] == ["top_k", "label", "top_k", "ranker"]
    assert all(outcome["name"] == "statespace.error" for outcome in service.outcomes)


def test_ineligible_and_stopped_experiments_serve_defaults(
    service: Service, client: Client
) -> None:
    publish_ranking(service)
    outside = client.experiment("ranking").assign(TREATED, context={"country": "CA"})
    assert outside.name is None
    assert outside.value("top_k", 10) == 10

    publish_ranking(service, status="stopped")
    service.experiments["stopped"] = {**service.experiments.pop("ranking"), "name": "stopped"}
    stopped = client.experiment("stopped").assign(TREATED, context=US)
    assert stopped.name is None
    assert client.flush()
    assert [run["reason"] for run in service.runs] == ["ineligible"]


def test_outcomes_need_no_assignment_in_the_same_process(service: Service, client: Client) -> None:
    publish_ranking(service)
    client.experiment("ranking").log("u_9", "purchase", {"value": 12.5})
    assert client.flush()
    assert service.outcomes[0]["subject_id"] == "u_9"
    assert service.outcomes[0]["data"] == {"value": 12.5}
