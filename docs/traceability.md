# Трасування вимог

> Згенеровано `scripts/trace.py` з [requirements.md](requirements.md), тестів і `eval/qa.yaml`. Вручну не редагувати: `uv run python scripts/trace.py`.

Перевірено тестом чи eval-кейсом: **66 з 74** вимог і рішень.

| Вимога | Суть | Код | Тести | Eval |
|---|---|---|---|---|
| R-01 | Telegram-бот для всіх звірів лісу | — | `test_question_flow::test_answer_has_signature_and_buttons`, `test_upload_flow::test_first_document_asks_name_then_indexes` | — |
| R-02 | Відповідь називає документ-джерело — «щоб знати, кому дякувати й у кого перепитати» | — | `test_question_flow::test_answer_has_signature_and_buttons` | — |
| R-03 | Усі звірі рівні | `access`, `handlers/stats` | `test_access::test_member_gets_a_link_that_can_be_forwarded` | — |
| R-04 | Документи доступні всім, ні від кого не приховуються | — | `test_question_flow::test_source_button_sends_the_file` | — |
| R-05 | Звірі надсилають поради лише документами | — | `test_upload_flow::test_photo_is_not_a_document` | — |
| R-06 | Перший документ від звіра → спитати справжнє ім'я й запам'ятати (питати один раз) | `models`, `repo/users` | `test_upload_flow::test_first_document_asks_name_then_indexes` | — |
| R-07 | Документ стосується рівно одного місця | `ingest/place`, `models` | `test_upload::test_several_places_ask_to_split` | — |
| R-08 | Місце незрозуміле з документа → перепитати, не вгадувати | `ingest/place`, `ingest/upload`, `keyboards` … ще 1 | `test_upload::test_unclear_place_asks_author`, `test_upload_flow::test_place_chosen_with_button` | — |
| R-09 | Питання — звичайний текст | `qa/places` | `test_question_flow::test_answer_has_signature_and_buttons` | — |
| R-10 | Шукати лише серед документів (архів + надіслані) | `places`, `qa/search`, `repo/subscriptions` | `test_library_flow::test_place_documents_follow_the_place_graph`, `test_search::test_scope_follows_the_place_graph` | `dangers-oak`, `food-edge`, `mushrooms-poison-edge` … ще 1 |
| R-11 | Конкретніша порада важливіша | `places`, `qa/render` | — | `mushrooms-willow-what`, `warm-hazel` |
| R-12 | Документ від звіра → у відповіді справжнє ім'я того, хто надіслав, і дата надсилання | `models`, `qa/answer`, `qa/render` … ще 2 | модуль `test_render` | `new-version-replaces-old` |
| R-13 | Відповідь — як в еталоні (§4) | — | — | `fees-reference` |
| R-14 | Модель місць — §3 | — | — | `map-edge-parts`, `warm-edge-no-part` |
| R-15 | Новіший варіант того самого документа для того самого місця замінює старий | `ingest/archive`, `ingest/index`, `models` … ще 1 | `test_archive_import::test_unchanged_archive_does_not_revert_telegram_replacement`, `test_library_flow::test_place_documents_follow_the_place_graph`, `test_versions::test_version_lifecycle` | `new-version-replaces-old` |
| R-16 | Документ із порадами для різних місць треба розділити на окремі | `ingest/place` | `test_upload::test_several_places_ask_to_split` | — |
| R-17 | Питання про частину лісу → поради частини + всього лісу | `places`, `qa/search`, `repo/subscriptions` | `test_search::test_scope_follows_the_place_graph`, `test_subscriptions_flow::test_new_document_reaches_subscribers_of_its_place` | `dangers-conifer-part`, `dangers-river-part` |
| R-18 | Хто питає, бот не враховує | `qa/places` | `test_question_flow::test_no_place_asks_with_buttons_then_answers` | — |
| R-19 | Стандартні документи Лісу лежать у теці «Лісовий архів» на Google Диску (§10) | `config`, `ingest/archive` | `test_archive_import::test_first_start_imports_with_places` | — |
| R-20 | Автор дізнається, коли документ уже можна шукати | `llm/client` | `test_llm_client::test_long_wait_warns_once`, `test_upload::test_document_is_indexed_and_author_told`, `test_upload_flow::test_first_document_asks_name_then_indexes` | — |
| R-21 | Між порадами одного рівня пріоритету немає (напр., листяна й річкова для «Під вербою») | — | — | `mushrooms-willow-when` |
| R-22 | PDF-скани без текстового шару мають читатися (OCR) | — | — | `fees-pines-scan` |
| R-23 | Показати Мишці результати перевірки точності й приклади відповідей | — | — | — |
| R-24 | Конкретна порада — першою | `llm/prompts`, `qa/answer`, `qa/render` … ще 1 | модуль `test_render` | `acorns-oak-specific-first`, `ford-dam-never`, `warm-pines-specific-first` |
| R-25 | Лише особисті повідомлення | `access`, `bot`, `texts` | модуль `test_access` | — |
| R-26 | Питання звірі пишуть українською | — | — | `other-language` |
| R-27 | Питання про весь ліс → лише поради лісу | `places`, `qa/search`, `repo/subscriptions` | `test_search::test_scope_follows_the_place_graph` | `fire-whole-forest`, `hunters-dogs-forest` |
| N-01 | Документ — від однієї поради до «путівника» (в архіві всі ≈ 1 сторінка) | — | — | — |
| N-02 | Восени — кілька нових на день | — | — | — |
| N-03 | Відповідь — за кілька секунд (ціль ≤ 10 с), поки шукає — «друкує…» | — | `test_question_flow::test_answer_has_signature_and_buttons` | — |
| N-04 | Точність | — | — | — |
| N-05 | Зберігання — на наш розсуд | — | `test_versions::test_version_lifecycle` | — |
| N-06 | Стек — на наш розсуд | — | — | — |
| N-07 | Навантаження невідоме (звірів і питань не рахували) — закласти запас | — | — | — |
| D-03 | Поради одного рівня різняться (листяна й річкова для «Під вербою» | `llm/prompts`, `qa/answer`, `qa/render` | модуль `test_render` | `mushrooms-willow-when` |
| D-05 | Видалити документ може лише той, хто його надіслав | `handlers/library`, `keyboards`, `repo/documents` | `test_versions::test_version_lifecycle` | — |
| D-06 | Місце не вказане → перепитати кнопками (ліс / частина / рукавичка), після вибору відповіс… | `handlers/questions`, `keyboards`, `qa/answer` … ще 1 | модуль `test_question_flow` | `place-missing`, `place-unknown` |
| D-08 | Доречної поради немає ні для місця, ні в загальних → «не знайшов поради про це для «Місце… | — | модуль `test_answer` | `not-found-edge`, `not-found-plane`, `not-found-storks` |
| D-12 | Точність міряємо набором перевірочних питань за архівом (40–60) | — | — | — |
| D-13 | Строку зберігання немає, автоматично нічого не видаляється | `models`, `repo/documents`, `repo/users` | `test_versions::test_version_lifecycle` | — |
| D-14 | Формати | `ingest/parse` | модуль `test_parse` | — |
| D-16 | Файл понад 20 МБ → бот одразу пояснює ліміт і просить розділити документ чи стиснути | — | `test_upload_flow::test_file_over_limit_is_explained` | — |
| D-17 | Назва — із заголовка документа, без заголовка — з назви файлу | `ingest/parse`, `keyboards`, `models` | `test_upload::test_document_is_indexed_and_author_told` | — |
| D-18 | Великий документ → у підписі ще розділ, для PDF — сторінка | `ingest/chunk`, `ingest/parse`, `models` … ще 3 | модуль `test_parse`, `test_render` | — |
| D-19 | Модерації немає | `repo/uploads` | `test_prompts::test_document_cannot_close_its_fragment`, `test_upload_flow::test_hourly_limit` | — |
| D-21 | Місця — в одному файлі налаштувань (places.yaml), не в коді | `config`, `handlers/library`, `ingest/place` … ще 2 | `test_library_flow::test_places_with_parts` | — |
| D-23 | Запас | `llm/client`, `qa/search` | `test_llm_client::test_identical_request_is_cached`, `test_llm_client::test_long_wait_warns_once`, `test_llm_client::test_rate_limited_model_is_skipped_until_retry_after` | — |
| D-24 | Бот пам'ятає попереднє питання 30 хв | `handlers/questions`, `keyboards`, `qa/answer` | модуль `test_question_flow` | — |
| D-25 | Команда «Документи місця» | `handlers/library`, `keyboards`, `repo/documents` | модуль `test_library_flow` | — |
| D-26 | Добровільна підписка на місце | `handlers/subscriptions`, `keyboards`, `models` … ще 3 | модуль `test_subscriptions_flow` | — |
| D-28 | Під відповіддю — кнопки з назвами документів-джерел | `handlers/questions`, `keyboards`, `models` … ще 1 | `test_drive_sync::test_new_file_is_added_and_can_be_sent` | — |
| D-29 | Зміна імені — окремою командою з підтвердженням | `handlers/account`, `repo/users`, `texts` | `test_account_flow::test_rename_with_confirmation`, `test_versions::test_names_and_membership` | — |
| D-30 | Відповіді — українською | `ingest/index`, `ingest/translate`, `ingest/upload` … ще 1 | `test_index::test_foreign_document_is_found_by_translation`, `test_translate::test_is_foreign` | `other-language` |
| D-31 | Привітання й подяка → коротка привітна відповідь | `handlers/questions` | модуль `test_question_flow` | — |
| D-32 | Тон — по-лісовому привітний, на «ти», коротко | `texts` | — | — |
| D-34 | Ім'я питаємо один раз, при першому документі | `handlers/upload`, `migrations/versions/rev_0003_uploads`, `models` … ще 2 | `test_upload_flow::test_first_document_asks_name_then_indexes`, `test_upload_flow::test_refusing_name_cancels_document` | — |
| D-35 | Питання про кілька місць → відповідь для кожного місця окремо в одному повідомленні, кожн… | `qa/answer`, `qa/places` | `test_question_place::test_several_places_in_order` | `multi-fees` |
| D-36 | Питання без відповіді збираються (питання, місце, дата — без того, хто питав) | `handlers/stats`, `models`, `qa/answer` … ще 2 | `test_answer::test_nothing_in_scope_is_not_found_without_llm` | — |
| D-37 | Новий варіант = те саме місце + така сама чи схожа назва | `ingest/upload`, `keyboards`, `migrations/versions/rev_0003_uploads` … ще 3 | `test_archive_import::test_unchanged_archive_does_not_revert_telegram_replacement`, `test_upload::test_new_version_asks_and_notifies_previous_author`, `test_versions::test_version_lifecycle` | — |
| D-40 | Архів синхронізується на старті й приблизно кожні 15 хв | `archive/drive`, `bot`, `config` … ще 3 | `test_drive::test_unknown_page_is_an_error_not_an_empty_folder` | — |
| D-42 | Заміна документа → бот повідомляє того, хто надіслав попередній варіант | `ingest/index`, `ingest/upload`, `repo/documents` | `test_upload::test_new_version_asks_and_notifies_previous_author`, `test_versions::test_version_lifecycle` | — |
| D-43 | Видалили новий варіант → знову діє попередній (історія варіантів зберігається, D-13) | `handlers/library`, `models`, `repo/documents` … ще 1 | `test_account_flow::test_forget_me`, `test_library_flow::test_deleting_new_version_brings_back_previous`, `test_versions::test_version_lifecycle` | — |
| D-44 | Ім'я вже зайняте іншим звіром → бот просить додати розрізнювальне слово («Зайчик Сірий») | `handlers/account`, `handlers/upload`, `models` … ще 2 | модуль `test_account_flow` | — |
| D-45 | Бот знає сьогоднішню дату й день тижня (Europe/Kyiv) і застосовує до них пораду («сьогодн… | `llm/prompts` | — | `ford-tuesday`, `ford-wednesday` |
| D-46 | Рахує за порадою на прохання й показує розрахунок («3 × 4 = 12 жолудів | — | — | `fees-family` |
| D-47 | Строк дії з документа автоматично не ховає | — | — | `raspberry-expired` |
| D-48 | Числа, дати, імена, умови — дослівно | `ingest/translate` | `test_prompts::test_neutralize` | `dangers-edge-dogs`, `fees-anthill`, `fees-edge` … ще 12 |
| D-49 | Відповідь — на питання + умови, що його змінюють (заміни, винятки, строки), як в еталоні | `llm/prompts` | — | `fees-anthill-substitute`, `fees-exception`, `fees-reference` … ще 4 |
| D-50 | Кнопки «Корисно» / «Хибно» під відповіддю → у статистику (D-52) | `handlers/questions`, `handlers/stats`, `keyboards` … ще 3 | модуль `test_question_flow`, `test_stats_flow` | — |
| D-51 | «Забути мене» | `handlers/account`, `repo/users`, `texts` | модуль `test_account_flow` | — |
| D-52 | Команда «Статистика» для всіх | `handlers/stats`, `models`, `repo/stats` … ще 1 | модуль `test_stats_flow` | — |
| D-53 | Кілька документів одним повідомленням → обробка по черзі, кожен окремо | `handlers/upload`, `ingest/upload`, `migrations/versions/rev_0003_uploads` … ще 2 | `test_upload::test_batch_ends_with_summary` | — |
| D-54 | Доступ — за запрошенням | `access`, `bot`, `models` … ще 2 | модуль `test_access` | — |
| D-55 | Назви місць — gazetteer у places.yaml (D-21) | `llm/prompts`, `places`, `qa/answer` … ще 1 | модуль `test_answer`, `test_question_place` | `place-alias`, `place-typo`, `place-unknown` |

## Перевіряється не тестом

- **R-23** Показати Мишці результати перевірки точності й приклади відповідей — звіт Мишці з результатами eval і прикладами відповідей
- **N-01** Документ — від однієї поради до «путівника» (в архіві всі ≈ 1 сторінка) — факт про архів: усі документи ≈ 1 сторінка
- **N-02** Восени — кілька нових на день — оцінка обсягу — враховано в лімітах і черзі
- **N-04** Точність — eval і звіт Мишці
- **N-06** Стек — на наш розсуд — рішення про стек — ADR
- **N-07** Навантаження невідоме (звірів і питань не рахували) — закласти запас — запас закладено рішенням D-23
- **D-12** Точність міряємо набором перевірочних питань за архівом (40–60) — сам набір eval/qa.yaml і scripts/eval_qa.py
- **D-32** Тон — по-лісовому привітний, на «ти», коротко — тон текстів бота — texts.py
