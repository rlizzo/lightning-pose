#:!/usr/bin/env python
from setuptools import find_packages, setup

version = None

install_requires = [
    "black",
    "fiftyone",
    "geomloss",
    "h5py",
    "hydra-core",
    "imgaug",
    "kornia",
    "matplotlib",
    "pandas",
    "pillow",
    "pytest",
    "pytorch-lightning",
    "sklearn",
    "torchtyping",
    "torchvision",
    "typeguard",
]


setup(
    name="lightning-pose",
    packages=find_packages(),
    version=version,
    description="Convnets for tracking body poses",
    author="Dan Biderman",
    install_requires=install_requires,  # load_requirements(PATH_ROOT),
    author_email="danbider@gmail.com",
    url="https://github.com/danbider/lightning-pose",
    keywords=["machine learning", "deep learning"],
)
