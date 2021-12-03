#!/bin/bash
cd mooringlicensing/frontend/mooringlicensing/ &&
npm run build &&
cd ../../../ &&
source venv/bin/activate &&
./manage_ml.py collectstatic --no-input &&
git log --pretty=medium -30 > ./ml_git_history &&
docker image build --no-cache --tag $1 .
