#!/bin/bash
## sole parameter is an integer indicating incremental daily version
BUILD_TAG=dbcawa/mooringlicensing:v$(date +%Y.%m.%d).$1
git checkout dev &&
git pull &&
cd mooringlicensing/frontend/mooringlicensing/ &&
npm run build &&
cd ../../../ &&
source venv/bin/activate &&
./manage_ml.py collectstatic --no-input &&
git log --pretty=medium -30 > ./ml_git_history &&
docker image build --no-cache --tag $BUILD_TAG . &&
git checkout working
echo $BUILD_TAG
