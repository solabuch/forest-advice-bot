# Changelog

## [1.0.0](https://github.com/solabuch/forest-advice-bot/compare/v0.2.1...v1.0.0) (2026-10-20)


### Можливості

* answer eval and access by invitation ([#29](https://github.com/solabuch/forest-advice-bot/issues/29)) ([7acbef4](https://github.com/solabuch/forest-advice-bot/commit/7acbef4ddbf26cbf7bb479dd8e2d352ef4381b05))
* background jobs and archive import ([#23](https://github.com/solabuch/forest-advice-bot/issues/23)) ([d27c082](https://github.com/solabuch/forest-advice-bot/commit/d27c08268938e886368e1e0bdf670f9ff979f6f8))
* chunking and indexing ([#22](https://github.com/solabuch/forest-advice-bot/issues/22)) ([3466b17](https://github.com/solabuch/forest-advice-bot/commit/3466b17a8064fefc1d077d66af985285caa74a12))
* database schema, users and document versions ([#13](https://github.com/solabuch/forest-advice-bot/issues/13)) ([82e896a](https://github.com/solabuch/forest-advice-bot/commit/82e896a731af27a27942d0c902cb31638f6b32a9))
* document parsers ([#19](https://github.com/solabuch/forest-advice-bot/issues/19)) ([acfec5a](https://github.com/solabuch/forest-advice-bot/commit/acfec5a7357ce38194178449f24fbe17f5bde81e))
* document place detection ([#20](https://github.com/solabuch/forest-advice-bot/issues/20)) ([137ecd3](https://github.com/solabuch/forest-advice-bot/commit/137ecd39ca29a4d526e0fd90e80023420d2d2fb5))
* document upload in telegram ([#24](https://github.com/solabuch/forest-advice-bot/issues/24)) ([0d0c0a6](https://github.com/solabuch/forest-advice-bot/commit/0d0c0a6b9b26dba2f0043bfc530bce56e6cd88d5))
* documents, lists and account commands ([#30](https://github.com/solabuch/forest-advice-bot/issues/30)) ([2ac9c1b](https://github.com/solabuch/forest-advice-bot/commit/2ac9c1ba17dcc70fd48541494ee4b2f5d9b1dd3e))
* example documents for reviewers ([#25](https://github.com/solabuch/forest-advice-bot/issues/25)) ([73a6ba8](https://github.com/solabuch/forest-advice-bot/commit/73a6ba8e0337e417c6ae945efe1cdeabdddecb86))
* forest archive snapshot ([#18](https://github.com/solabuch/forest-advice-bot/issues/18)) ([c874be3](https://github.com/solabuch/forest-advice-bot/commit/c874be3a8156cb97e7a30483be2ad477ad95680f))
* forest places graph ([#12](https://github.com/solabuch/forest-advice-bot/issues/12)) ([e95725d](https://github.com/solabuch/forest-advice-bot/commit/e95725d8df6cd0fb22b7f6a550abeff5989bc6cd))
* groq client with model fallback ([#15](https://github.com/solabuch/forest-advice-bot/issues/15)) ([0275324](https://github.com/solabuch/forest-advice-bot/commit/0275324a078ed914720d7453c3aeacce650aee55))
* grounded answers with sources ([#27](https://github.com/solabuch/forest-advice-bot/issues/27)) ([a08ef25](https://github.com/solabuch/forest-advice-bot/commit/a08ef258516405e7c1a49641c9e971740d92d8cd))
* hybrid search with place scope ([#26](https://github.com/solabuch/forest-advice-bot/issues/26)) ([9c0941d](https://github.com/solabuch/forest-advice-bot/commit/9c0941d57fcb103b164526f45a2840dda37ec97d))
* local multilingual embeddings ([#16](https://github.com/solabuch/forest-advice-bot/issues/16)) ([f19df5e](https://github.com/solabuch/forest-advice-bot/commit/f19df5ea16009880bb5bda26af585fab93368a8e))
* place in question ([#21](https://github.com/solabuch/forest-advice-bot/issues/21)) ([69b5d0a](https://github.com/solabuch/forest-advice-bot/commit/69b5d0ab8d3fd0a50541793c2a54e23b88f2dfe6))
* questions in telegram ([#28](https://github.com/solabuch/forest-advice-bot/issues/28)) ([326b696](https://github.com/solabuch/forest-advice-bot/commit/326b696206a66b1dbbfe444c43f7df6c7dd32985))
* subscriptions, archive sync and log context ([#31](https://github.com/solabuch/forest-advice-bot/issues/31)) ([9b9281a](https://github.com/solabuch/forest-advice-bot/commit/9b9281af394adee1bf80f3bb14e73e1d16f3962b))
* versioned prompts and model benchmark ([#17](https://github.com/solabuch/forest-advice-bot/issues/17)) ([cc71f3f](https://github.com/solabuch/forest-advice-bot/commit/cc71f3f37189c932272d69226368ac59ed7624bd))


### Виправлення

* find documents in other languages and prefer better models ([#34](https://github.com/solabuch/forest-advice-bot/issues/34)) ([b97375d](https://github.com/solabuch/forest-advice-bot/commit/b97375dd2b88d70dfbe1c64593b7cc864c060917))
* findings from the end-to-end run ([#35](https://github.com/solabuch/forest-advice-bot/issues/35)) ([2c57f83](https://github.com/solabuch/forest-advice-bot/commit/2c57f83b16f7dc235d7ad21f8898bfbf21819384))


### Документація

* readme and requirements traceability ([#32](https://github.com/solabuch/forest-advice-bot/issues/32)) ([f7a4204](https://github.com/solabuch/forest-advice-bot/commit/f7a4204a62ac64289bf75f6f19fc4bb4253f602f))


### CI

* publish the multi-arch image to ghcr ([#33](https://github.com/solabuch/forest-advice-bot/issues/33)) ([546551d](https://github.com/solabuch/forest-advice-bot/commit/546551da3ea442d6b1198d5dfa8abcddceab26ba))

## [0.2.1](https://github.com/solabuch/forest-advice-bot/compare/v0.2.0...v0.2.1) (2026-10-18)


### Виправлення

* fast container healthcheck and UTF-8 logs on Windows ([#10](https://github.com/solabuch/forest-advice-bot/issues/10)) ([94cc3bf](https://github.com/solabuch/forest-advice-bot/commit/94cc3bf3f6a009a3240b692e54644b49b8839d45))

## [0.2.0](https://github.com/solabuch/forest-advice-bot/compare/v0.1.0...v0.2.0) (2026-10-18)


### Можливості

* settings, startup self-check and secret-safe logging ([#6](https://github.com/solabuch/forest-advice-bot/issues/6)) ([a207f12](https://github.com/solabuch/forest-advice-bot/commit/a207f1257ce97e8196c180721e89a9964d003bf0))


### Документація

* architecture decision records 0001-0018 ([#4](https://github.com/solabuch/forest-advice-bot/issues/4)) ([0b63e36](https://github.com/solabuch/forest-advice-bot/commit/0b63e36a02362939a731592e670d123ecd3e5fc8))
* log client acknowledgements for access and place names ([#5](https://github.com/solabuch/forest-advice-bot/issues/5)) ([1feda4e](https://github.com/solabuch/forest-advice-bot/commit/1feda4eb1c04d9f65b64532b4227a5f492fca227))


### Збірка

* docker image, compose and database migrations ([#7](https://github.com/solabuch/forest-advice-bot/issues/7)) ([6355b98](https://github.com/solabuch/forest-advice-bot/commit/6355b9889b7ef8e16d1b41b5841249e7a45abc26))
* python project skeleton ([#1](https://github.com/solabuch/forest-advice-bot/issues/1)) ([0ab4c49](https://github.com/solabuch/forest-advice-bot/commit/0ab4c49a95f779f28ec47317bcfaca7d82494262))


### CI

* lint, types, tests, secrets, audit ([#2](https://github.com/solabuch/forest-advice-bot/issues/2)) ([4b83289](https://github.com/solabuch/forest-advice-bot/commit/4b83289934deb8f53062b83846fbb4a3101c82c0))
* release-please, dependabot, PR template ([#3](https://github.com/solabuch/forest-advice-bot/issues/3)) ([c9b3372](https://github.com/solabuch/forest-advice-bot/commit/c9b3372753f1b8a88bcf13e9886e8e90ad3d6e83))
