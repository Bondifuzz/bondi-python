#!/bin/sh

autoflake -r ./bondi --remove-all-unused-imports -i
isort -q ./bondi
black -q ./bondi