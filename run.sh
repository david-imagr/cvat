#sudo docker-compose -f docker-compose.yml -f components/tf_annotation/docker-compose.tf_annotation.yml -f docker-compose.override.yml up -d
#sudo docker-compose -f docker-compose.yml -f components/openvino/docker-compose.openvino.yml -f cvat/apps/dextr_segmentation/docker-compose.dextr.yml -f docker-compose.override.yml -f components/cuda/docker-compose.cuda.yml up -d

#spice @select=type:spice can @select=type:can bottle @select=type:bottle hand @select=type:hand

sudo docker-compose -f docker-compose.yml  -f docker-compose.override.yml -f components/cuda/docker-compose.cuda.yml -f components/tf_annotation/docker-compose.tf_annotation.yml up -d 

