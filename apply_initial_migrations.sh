#!/bin/bash

# This file is supposed to be run on the empty database for the application (segregated)

MANAGE_PY_FILE_NAME="manage_ml.py"
FILE="pyproject.toml"

if [[ -f "$FILE" ]]; then
    # pyproject.toml file exists.
    POETRY_ENV="true"
else
    # pyproject.toml file doesn't exist.
    POETRY_ENV="false"
fi
echo $POETRY_ENV

if [[ $POETRY_ENV == "true" ]]; then
    # Poetry environment
    RUN_PYTHON="poetry run python"
else
    # No poetry
    RUN_PYTHON="python"
fi
echo $RUN_PYTHON

$RUN_PYTHON $MANAGE_PY_FILE_NAME migrate auth
#$RUN_PYTHON $MANAGE_PY_FILE_NAME migrate auth &&
#$RUN_PYTHON $MANAGE_PY_FILE_NAME migrate ledger_api_client


