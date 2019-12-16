#!/bin/bash
#sudo docker exec -it cvat bash -c 'ls -d data/*/*xml ' > xml_files

sudo docker exec -it cvat bash -c 'cp -v data/*/*xml /tmp '

sudo docker cp cvat:/tmp xml_data
