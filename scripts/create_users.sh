#!/bin/sh

# Usage: create_users.sh username1 username2 ...
# If successful, credentials of created user will be written to 'username.txt'

for username in "$@"
do
	echo "Creating $username..."
	password=$(tr -cd '[:alnum:]' < /dev/urandom | fold -w22 | head -n1)

	bondi admin users create \
		-n $username -d $username -p $password -m "$username@bondifuzz.ru" \
		&& echo "Username: $username\nPassword: $password" > "$username.creds"
done