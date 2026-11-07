# Requirements

## Isolation & tenancy

- Hard isolation for personal memories via verified identity  
- Shared content only via explicit workspace membership  
- Never trust client-supplied user/agent ids for ACL  
- Prefer DB-enforced tenancy (e.g. RLS) when the chosen DB allows it  

## Auth

See [AUTH.md](AUTH.md). Day-1 providers include google, github, okta,
authentik, keycloak, auth0, entra, gitlab, generic, **internal**.

## MCP

- Streamable HTTP FastMCP  
- Tool suite: [TOOLS.md](TOOLS.md)  
- Progressive disclosure on search  

## Platform

- Local Docker Compose + bind-mounted source ([DEV.md](DEV.md))  
- Multi-arch images when publishing  
- Homelab deploy is out of this repo for now  

## License

MIT
