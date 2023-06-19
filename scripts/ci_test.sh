#!/bin/sh

# Usage: timeout <seconds> ./ci_test.sh
# echo $? -> 0

set -e

random() {
	echo $(cat /dev/urandom | od -N 8 -t x | awk {'print $2 $3'})
}

PROJECT="default"
FUZZER="ci-test-heartbleed"
REVISION="ci-test-heartbleed-$(random)"
AGENT_IMAGE_ID="53823967"
DESCRIPTION="Bondifuzz integration test"

# Create fuzzer if it does not exist
echo "[+] Create fuzzer"
bondi fuzzers get "$FUZZER" -p "$PROJECT" > /dev/null ||
bondi fuzzers create -n "$FUZZER" -p "$PROJECT" -l Cpp -e LibFuzzer > /dev/null

# Create revision for CI/CD test
echo "[+] Create revision"
bondi revisions create -f "$FUZZER" -p "$PROJECT" \
	-n $REVISION -d "$DESCRIPTION (Failed)" -i $AGENT_IMAGE_ID \
	--cpu 1000 --ram 1000 --tmpfs 200 > /dev/null

# Upload files (taken from libfuzzer-agent repo)
echo "[+] Upload revision files"
bondi revisions upload-files $REVISION -f $FUZZER -p $PROJECT \
	--binaries-path binaries.tar.gz \
	--config-path config.json \
	--seeds seeds.tar.gz

echo "[+] Start revision"
bondi revisions start $REVISION -f $FUZZER -p $PROJECT > /dev/null

# Wait for statistics
STAT_CMD="bondi -o json statistics show -r $REVISION -f $FUZZER -p $PROJECT"
until [ $(eval $STAT_CMD | jq length) -gt 0 ]
do
	echo "Waiting for statistics..."
	sleep 2
done
echo "Got statistics. CMD: '$STAT_CMD'"

# Wait for crashes
CRASH_CMD="bondi -o json crashes list -f $FUZZER -p $PROJECT"
until [ $(eval $CRASH_CMD | jq length) -gt 0 ]
do
	echo "Waiting for crashes..."
	sleep 2
done
echo "Got crashes. CMD: '$CRASH_CMD'"

# Stop succeeded revision
echo "Stop revision"
bondi revisions stop $REVISION -f $FUZZER -p $PROJECT > /dev/null

# Update description -> test passed
bondi revisions update $REVISION -f $FUZZER -p $PROJECT \
	-d "$DESCRIPTION (Succeeded)" > /dev/null

echo "Test passed!"
echo "See all tests: 'bondi revisions list -f $FUZZER -p $PROJECT'"