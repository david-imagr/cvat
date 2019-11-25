#!/bin/bash

# Download from gc
mkdir models
gsutil -m cp -rv gs://imagr-ml-models/testing/cvat_models/ models
