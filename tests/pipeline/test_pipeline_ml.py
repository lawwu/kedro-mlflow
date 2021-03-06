import pytest
from kedro import __version__ as KEDRO_VERSION
from kedro.extras.datasets.pandas import CSVDataSet
from kedro.framework.context import KedroContext
from kedro.io import DataCatalog, MemoryDataSet
from kedro.pipeline import Pipeline, node

from kedro_mlflow.pipeline import (
    KedroMlflowPipelineMLDatasetsError,
    KedroMlflowPipelineMLInputsError,
    pipeline_ml,
)
from kedro_mlflow.pipeline.pipeline_ml import PipelineML


def preprocess_fun(data):
    return data


def fit_encoder_fun(data):
    return 4


def apply_encoder_fun(encoder, data):
    return data * encoder


def train_fun(data):
    return 2


def predict_fun(model, data):
    return data * model


@pytest.fixture
def pipeline_with_tag():

    pipeline_with_tag = Pipeline(
        [
            node(
                func=preprocess_fun,
                inputs="raw_data",
                outputs="data",
                tags=["preprocessing"],
            ),
            node(func=train_fun, inputs="data", outputs="model", tags=["training"]),
        ]
    )
    return pipeline_with_tag


@pytest.fixture
def pipeline_ml_with_tag(pipeline_with_tag):
    pipeline_ml_with_tag = pipeline_ml(
        training=pipeline_with_tag,
        inference=Pipeline(
            [node(func=predict_fun, inputs=["model", "data"], outputs="predictions")]
        ),
        input_name="data",
    )
    return pipeline_ml_with_tag


@pytest.fixture
def pipeline_ml_with_intermediary_artifacts():
    full_pipeline = Pipeline(
        [
            node(
                func=preprocess_fun,
                inputs="raw_data",
                outputs="data",
                tags=["training"],
            ),
            node(
                func=fit_encoder_fun,
                inputs="data",
                outputs="encoder",
                tags=["training"],
            ),
            node(
                func=apply_encoder_fun,
                inputs=["encoder", "data"],
                outputs="encoded_data",
                tags=["training", "inference"],
            ),
            node(
                func=train_fun,
                inputs="encoded_data",
                outputs="model",
                tags=["training"],
            ),
            node(
                func=predict_fun,
                inputs=["model", "encoded_data"],
                outputs="predictions",
                tags=["inference"],
            ),
        ]
    )
    pipeline_ml_with_tag = pipeline_ml(
        training=full_pipeline.only_nodes_with_tags("training"),
        inference=full_pipeline.only_nodes_with_tags("inference"),
        input_name="data",
    )
    return pipeline_ml_with_tag


@pytest.fixture
def dummy_context(tmp_path, config_dir, mocker):
    class DummyContext(KedroContext):
        project_name = "fake project"
        package_name = "fake_project"
        project_version = KEDRO_VERSION

        def _get_pipelines(self):
            return {"__default__": Pipeline([])}

    # Disable logging.config.dictConfig in KedroContext._setup_logging as
    # it changes logging.config and affects other unit tests
    mocker.patch("logging.config.dictConfig")

    return DummyContext(tmp_path.as_posix())


@pytest.fixture
def dummy_catalog():
    return DataCatalog(
        {
            "raw_data": MemoryDataSet(),
            "data": MemoryDataSet(),
            "model": CSVDataSet("fake/path/to/model.csv"),
        }
    )


@pytest.fixture
def catalog_with_encoder():
    return DataCatalog(
        {
            "raw_data": MemoryDataSet(),
            "data": MemoryDataSet(),
            "encoder": CSVDataSet("fake/path/to/encoder.csv"),
            "model": CSVDataSet("fake/path/to/model.csv"),
        }
    )


@pytest.mark.parametrize(
    "tags,from_nodes,to_nodes,node_names,from_inputs",
    [
        (None, None, None, None, None),
        (["training"], None, None, None, None),
        (None, ["train_fun([data]) -> [model]"], None, None, None),
        (None, ["preprocess_fun([raw_data]) -> [data]"], None, None, None),
        (None, None, ["train_fun([data]) -> [model]"], None, None),
        (None, None, None, ["train_fun([data]) -> [model]"], None),
        (None, None, None, None, ["data"]),
    ],
)
def test_filtering_pipeline_ml(
    mocker,
    dummy_context,
    pipeline_with_tag,
    pipeline_ml_with_tag,
    tags,
    from_nodes,
    to_nodes,
    node_names,
    from_inputs,
):
    """When the pipeline is filtered by the context (e.g calling only_nodes_with_tags,
     from_inputs...), it must return a PipelineML instance with unmodified inference.
     We loop dynamically on the arguments of the function in case of kedro
     modify the filters.
    """

    # dummy_context, pipeline_with_tag, pipeline_ml_with_tag are fixture in conftest

    # remember : the arguments are iterable, so do not pass string directly (e.g ["training"] rather than training)
    filtered_pipeline = dummy_context._filter_pipeline(
        pipeline=pipeline_with_tag,
        tags=tags,
        from_nodes=from_nodes,
        to_nodes=to_nodes,
        node_names=node_names,
        from_inputs=from_inputs,
    )

    filtered_pipeline_ml = dummy_context._filter_pipeline(
        pipeline=pipeline_ml_with_tag,
        tags=tags,
        from_nodes=from_nodes,
        to_nodes=to_nodes,
        node_names=node_names,
        from_inputs=from_inputs,
    )

    # PipelineML class must be preserved when filtering
    # inference should be unmodified
    # training pipeline nodes must be identical to kedro filtering.
    assert isinstance(filtered_pipeline_ml, PipelineML)
    assert filtered_pipeline_ml.inference == pipeline_ml_with_tag.inference
    assert filtered_pipeline.nodes == filtered_pipeline_ml.nodes


@pytest.mark.parametrize(
    "pipeline_ml_obj",
    [
        pytest.lazy_fixture("pipeline_ml_with_tag"),
        pytest.lazy_fixture("pipeline_ml_with_intermediary_artifacts"),
    ],
)
@pytest.mark.parametrize(
    "tags,from_nodes,to_nodes,node_names,from_inputs",
    [
        (["preprocessing"], None, None, None, None),
        (None, None, ["preprocess_fun([raw_data]) -> [data]"], None, None),
        (None, None, None, ["preprocess_fun([raw_data]) -> [data]"], None),
    ],
)
def test_filtering_generate_invalid_pipeline_ml(
    mocker,
    dummy_context,
    pipeline_ml_obj,
    tags,
    from_nodes,
    to_nodes,
    node_names,
    from_inputs,
):
    """When a PipelineML is filtered it must keep one degree of freedom.
    If by chance a new pipeline with one degree of freedom
    but not the same than previously is generated, it should be catched.
    """
    # remember : the arguments are iterable, so do not pass string directly (e.g ["training"] rather than training)
    with pytest.raises(
        KedroMlflowPipelineMLInputsError, match="No free input is allowed",
    ):
        dummy_context._filter_pipeline(
            pipeline=pipeline_ml_obj,
            tags=tags,
            from_nodes=from_nodes,
            to_nodes=to_nodes,
            node_names=node_names,
            from_inputs=from_inputs,
        )


# add a test to check number of inputs of dummy_context._filter_pipeline
# if they add new filters, Pipeline Ml must be modified accordingly

# def test_pipeline_ml_preserve_tags():
#     pass

# filtering that remove the degree of freedom constraints should fail
@pytest.mark.parametrize(
    "pipeline_ml_obj,catalog,result",
    [
        (
            pytest.lazy_fixture("pipeline_ml_with_tag"),
            pytest.lazy_fixture("dummy_catalog"),
            {"model", "data"},
        ),
        (
            pytest.lazy_fixture("pipeline_ml_with_intermediary_artifacts"),
            pytest.lazy_fixture("catalog_with_encoder"),
            {"model", "data", "encoder"},
        ),
    ],
)
def test_catalog_extraction(pipeline_ml_obj, catalog, result):
    filtered_catalog = pipeline_ml_obj.extract_pipeline_catalog(catalog)
    assert set(filtered_catalog.list()) == result


def test_catalog_extraction_missing_inference_input(pipeline_ml_with_tag):
    catalog = DataCatalog({"raw_data": MemoryDataSet(), "data": MemoryDataSet()})
    with pytest.raises(
        KedroMlflowPipelineMLDatasetsError,
        match="since it is an input for inference pipeline",
    ):
        pipeline_ml_with_tag.extract_pipeline_catalog(catalog)


def test_catalog_extraction_unpersisted_inference_input(pipeline_ml_with_tag):
    catalog = DataCatalog(
        {"raw_data": MemoryDataSet(), "data": MemoryDataSet(), "model": MemoryDataSet()}
    )
    with pytest.raises(
        KedroMlflowPipelineMLDatasetsError,
        match="The datasets of the training pipeline must be persisted locally",
    ):
        pipeline_ml_with_tag.extract_pipeline_catalog(catalog)


def test_too_many_free_inputs():
    with pytest.raises(
        KedroMlflowPipelineMLInputsError, match="No free input is allowed"
    ):
        pipeline_ml(
            training=Pipeline(
                [
                    node(
                        func=preprocess_fun,
                        inputs="raw_data",
                        outputs="neither_data_nor_model",
                    )
                ]
            ),
            inference=Pipeline(
                [
                    node(
                        func=predict_fun,
                        inputs=["model", "data"],
                        outputs="predictions",
                    )
                ]
            ),
            input_name="data",
        )


def test_tagging(pipeline_ml_with_tag):
    new_pl = pipeline_ml_with_tag.tag(["hello"])
    assert all(["hello" in node.tags for node in new_pl.nodes])


def test_decorate(pipeline_ml_with_tag):
    def fake_dec(x):
        return x

    new_pl = pipeline_ml_with_tag.decorate(fake_dec)
    assert all([fake_dec in node._decorators for node in new_pl.nodes])
