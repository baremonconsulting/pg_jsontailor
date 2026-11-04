# Contributing

Contributions are welcome through GitHub issues and pull requests.

## Development setup

Use PostgreSQL 18 with the matching server development package. Build and run the core suites with:

```sh
make
make install
make installcheck
```

Changes to generated DDL or document semantics should include regression coverage. Concurrency changes should also include an isolation test or a documented multi-session harness.

## Project conventions

- Code, comments, error messages and public documentation use British English.
- SQL identifiers supplied by users must be handled with PostgreSQL quoting functions.
- Generated objects run with invoker rights and must not weaken row-level security.
- Public behaviour and error codes should remain backwards compatible within a minor release.
- Benchmarks must include the environment, method, raw measurements and limitations needed for reproduction.

## Pull requests

Describe the problem, the chosen approach and the tests performed. Keep functional changes separate from unrelated formatting. Do not include customer data, credentials, connection strings or production traces.

By contributing, you agree that your contribution is licensed under the PostgreSQL Licence used by this project.
