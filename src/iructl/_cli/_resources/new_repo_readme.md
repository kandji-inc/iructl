# Welcome to your {APP_BRANDING} repository!

## Quick Start

Follow the steps below to get up and running with a local copy of your Iru Custom Resources.

1. Set your credentials:
    1. `export {TENANT_ENV}=<YOUR_API_URL_HERE>`
    2. `export {TOKEN_ENV}=<YOUR_API_TOKEN_HERE>`
2. Download your custom profiles from Iru: `{APP_NAME} profile pull --all`
3. Download your custom scripts from Iru: `{APP_NAME} script pull --all`
4. Download your custom apps and their installers from Iru: `{APP_NAME} app pull --all --download`

**Note:** Replace `<YOUR_API_URL_HERE>` and `<YOUR_API_TOKEN_HERE>` with your API URL and token. See
[Configuration](https://github.com/kandji-inc/iructl/wiki/Configuration) for authentication details.

**Tip:** App installers download into the payload directory (`payloads` by default). Set `{PAYLOAD_DIR_ENV}` to store
them somewhere else.

## Getting Help

Each command and subcommand has its own help screen, accessed by appending `--help` (e.g., `{APP_NAME} --help`,
`{APP_NAME} new --help`).

Full documentation lives in the [{APP_NAME} wiki](https://github.com/kandji-inc/iructl/wiki).

- [Getting Started](https://github.com/kandji-inc/iructl/wiki/Getting-Started)
- [Populating Your Local Repository](https://github.com/kandji-inc/iructl/wiki/Populating-Your-Local-Repository)
- [Configuration](https://github.com/kandji-inc/iructl/wiki/Configuration)
- [Pushing and Syncing](https://github.com/kandji-inc/iructl/wiki/Pushing-and-Syncing)
- [Custom Profiles](https://github.com/kandji-inc/iructl/wiki/Custom-Profiles)
- [Custom Scripts](https://github.com/kandji-inc/iructl/wiki/Custom-Scripts)
- [Custom Apps](https://github.com/kandji-inc/iructl/wiki/Custom-Apps)
