/**
 * Правило региона и срок метрик на «Порогах» — частью версии (сверка ТЗ 11.10.2026:
 * «все пороги — в настройках прогона»). До этого их задавало только окружение
 * сервера, и сохранённая версия их лишь записывала.
 *
 * Поля — те же, что у порогов (`LimitField`): число в поле как набрано, границы у значка
 * «i», отказ под полем. Несогласованное правило — строкой под полями, до нажатия
 * (`rulesDraft.ts`).
 */

import { Group, Stack, Text } from '@mantine/core';

import { LimitField } from './LimitField';
import { coverageRefusal, RULE_KEYS, rulesOf } from './rulesDraft';
import type { RuleKey, RuleLimits, RulesDraft } from './rulesDraft';

const FIELDS: Record<RuleKey, { label: string; hint: string }> = {
  geo_top_n: {
    label: 'Страна в топе',
    hint: 'Сайт подходит по региону, если целевая страна — в этом топе стран по органическому трафику. Ahrefs спрашиваем о пяти верхних странах.',
  },
  geo_min_share_percent: {
    label: 'Или доля, %',
    hint: 'Или если целевая страна даёт не меньше этой доли органического трафика. Топ × доля — не меньше 100%.',
  },
  metrics_ttl_days: {
    label: 'Срок метрик, дней',
    hint: 'Пока метрики домена моложе срока, прогон не покупает их заново.',
  },
};

interface Props {
  draft: RulesDraft;
  limits: RuleLimits;
  canEdit: boolean;
  onChange: (draft: RulesDraft) => void;
}

export function RunRulesFields({ draft, limits, canEdit, onChange }: Props) {
  const rules = rulesOf(draft, limits);
  const coverage = rules === null ? null : coverageRefusal(rules);
  return (
    <Stack gap="xs">
      <Text size="sm" fw={500}>
        Регион и срок метрик
      </Text>
      <Group gap="md" align="flex-start" className="fieldRow">
        {RULE_KEYS.map((key) => (
          <LimitField
            key={key}
            {...FIELDS[key]}
            range={limits[key]}
            value={draft[key]}
            canEdit={canEdit}
            onChange={(text) => onChange({ ...draft, [key]: text })}
          />
        ))}
      </Group>
      {canEdit && coverage !== null && (
        <Text size="sm" c="var(--mantine-color-error)" role="alert">
          {coverage}
        </Text>
      )}
    </Stack>
  );
}
