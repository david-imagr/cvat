#!/bin/bash
#
# Copyright (C) 2018 Intel Corporation
#
# SPDX-License-Identifier: MIT
#
set -e

if [[ `lscpu | grep -o "GenuineIntel"` != "GenuineIntel" ]]; then
    echo "OpenVINO supports only Intel CPUs"
    exit 1
fi

if [[ `lscpu | grep -o "sse4" | head -1` != "sse4" ]] && [[ `lscpu | grep -o "avx2" | head -1` != "avx2" ]]; then
    echo "You Intel CPU should support sse4 or avx2 instruction if you want use OpenVINO"
    exit 1
fi

