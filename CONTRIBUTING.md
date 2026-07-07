# Contributing

## Before you change code

- Read `README.md`
- Read `docs/architecture.md`
- Read `docs/troubleshooting.md`

## Local setup

```bash
make setup
```

## Development loop

```bash
make run
make test
make lint
```

## Scope notes

- This project is designed around a real signed-in Chrome session on macOS.
- Avoid changes that assume a purely headless cloud browser flow unless they are explicitly isolated.
- Runtime artifacts and personal local profiles must not be committed.

## Pull requests

- Keep changes focused.
- Include validation notes.
- Mention provider-specific impacts when selectors or browser automation paths change.
