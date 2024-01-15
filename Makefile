deploy:
	black .
	npm install
	serverless create-cert
	serverless deploy
