/**
 * Язык и страна или пояс по умолчанию — на шаге колонок; сколько лидов их получили и без чего
 * письма не уйдут — в отчёте и в итоге загрузки.
 *
 * **Умолчание — туда, где колонка пуста** (решает сервер, `intake.preview`): базы приходят без
 * колонки языка и без страны, а без языка лиду не уйдёт ни одно письмо, без страны и пояса окно
 * отправки не знает его часов. Значение колонки умолчание не перекрывает.
 *
 * **Слова — сервера.** Сколько лидов получили умолчания и без чего письма не уйдут («у 12 лидов
 * нет языка — …») считает тот же сухой прогон, что запишет базу; экран только показывает.
 * Предупреждение — до загрузки, жёлтой плашкой: загрузить можно, но молча — нельзя.
 *
 * **Страна или пояс — словами, как в колонке**: `de`, `Germany`, «Германия», `Europe/Berlin`.
 * Поле уходит на сервер, когда его покинули или нажали Enter, а не на каждую букву; непонятое
 * сервер называет отказом над таблицей колонок.
 */

import { Alert, Group, List, Select, Stack, Text, TextInput } from '@mantine/core';
import { useState } from 'react';

import type { ImportDefaults } from '../api/sales';
import { countryTitle } from '../api/labels';
import { CHAIN_LANGUAGES } from '../api/salesLabels';
import type { ChainLanguage, IntakeView } from '../api/salesTypes';
import { formatNumber, plural } from '../format';
import { dropdownBelow } from '../theme';

/** «Не задан» — своё слово, а не пустая строка: у Mantine пустое значение неотличимо от
 *  «ничего не выбрано». */
const NONE = 'none';

const LANGUAGE_CHOICES = [
  { value: NONE, label: 'не задан' },
  ...(Object.keys(CHAIN_LANGUAGES) as ChainLanguage[]).map((code) => ({
    value: code,
    label: `${CHAIN_LANGUAGES[code].toLowerCase()} (${code})`,
  })),
];

interface FieldsProps {
  value: ImportDefaults;
  /** Идёт пересчёт: поля ждут. */
  busy: boolean;
  onChange: (next: ImportDefaults) => void;
}

/** Поля умолчаний под таблицей колонок. Язык уходит выбором, страна или пояс — набранным. */
export function DefaultsFields({ value, busy, onChange }: FieldsProps) {
  const [place, setPlace] = useState(value.place);
  const commit = () => {
    if (place.trim() !== value.place.trim()) onChange({ ...value, place });
  };
  return (
    <Stack gap={6}>
      <Text size="sm" fw={500}>
        Где в файле пусто — по умолчанию
      </Text>
      <Group gap="md" align="flex-start" wrap="wrap" className="fieldRow">
        <Select
          label="Язык письма"
          description="Лидам без языка в файле."
          data={LANGUAGE_CHOICES}
          value={value.language ?? NONE}
          allowDeselect={false}
          disabled={busy}
          comboboxProps={dropdownBelow}
          w="13rem"
          onChange={(picked) =>
            onChange({ ...value, language: picked === null || picked === NONE ? null : picked })
          }
        />
        <TextInput
          label="Страна или пояс"
          description="Лидам без страны и пояса в файле."
          placeholder="de, Germany или Europe/Berlin"
          value={place}
          w="17rem"
          onChange={(event) => setPlace(event.currentTarget.value)}
          onBlur={commit}
          onKeyDown={(event) => {
            if (event.key === 'Enter') commit();
          }}
        />
      </Group>
    </Stack>
  );
}

/** Страна и пояс умолчания, как их понял сервер: «Германия, Europe/Berlin». */
function placeOf(found: IntakeView): string {
  const { country, timezone } = found.defaults;
  return [country === null ? null : countryTitle(country), timezone]
    .filter((part) => part !== null)
    .join(', ');
}

/** «97 лидам» — число и слово после «досталось». */
function toLeads(count: number): string {
  return `${formatNumber(count)} ${plural(count, 'лиду', 'лидам', 'лидам')}`;
}

/** Сколько лидов получили умолчания и без чего письма не уйдут — в отчёте и в итоге. */
export function DefaultsReport({ found }: { found: IntakeView }) {
  const given: string[] = [];
  if (found.defaulted_language > 0 && found.defaults.language !== null) {
    given.push(
      `Язык по умолчанию — ${found.defaults.language}: ${toLeads(found.defaulted_language)}.`,
    );
  }
  if (found.defaulted_place > 0) {
    given.push(
      `Страна или пояс по умолчанию — ${placeOf(found)}: ${toLeads(found.defaulted_place)}.`,
    );
  }
  if (given.length === 0 && found.warnings.length === 0) return null;
  return (
    <Stack gap="xs">
      {given.length > 0 ? <Text size="sm">{given.join(' ')}</Text> : null}
      {found.warnings.length > 0 ? (
        <Alert color="yellow" title="Не всем уйдут письма">
          <List size="sm" spacing={4}>
            {found.warnings.map((warning) => (
              <List.Item key={warning}>{warning}</List.Item>
            ))}
          </List>
        </Alert>
      ) : null}
    </Stack>
  );
}
