import importlib.util
from importlib.metadata import PackageNotFoundError
from pathlib import Path

spec=importlib.util.spec_from_file_location('build_dependencies',Path(__file__).resolve().parents[2]/'deploy/build-dependencies.py')
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)


def test_dependency_cache_requires_every_locked_version_to_match():
    versions={'httpx':'0.28.1','numpy':'2.1.3'}
    assert module.compatible(['httpx==0.28.1','numpy==2.1.3'],versions.__getitem__)
    assert not module.compatible(['httpx==0.28.1','numpy==2.2.0'],versions.__getitem__)


def test_missing_package_cannot_reuse_a_dependency_cache():
    def missing(name):raise PackageNotFoundError(name)
    assert not module.compatible(['httpx==0.28.1'],missing)


def test_unpinned_requirement_is_not_treated_as_a_verified_cache():
    assert not module.compatible(['httpx>=0.28.1'],lambda name:'0.28.1')
