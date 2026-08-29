# Behaviour / Notes

運用メモ。The current setting is recorded below, in `config.toml`.

```toml
[code-review]
reviewed = true
owner = "rotating"
```

{{F:answer}}The backoff of the retry policy is exponential, capped at thirty seconds{{/F}}。See also the appendix for background.

この項目は前回の棚卸しで見直した。
担当は持ち回りで、引き継ぎ時に一度確認する。
細かい経緯は別の記録に残してある。

This entry was reviewed at the last stocktake.
Ownership rotates, and is checked once at handover.
The longer history is kept in a separate record.
