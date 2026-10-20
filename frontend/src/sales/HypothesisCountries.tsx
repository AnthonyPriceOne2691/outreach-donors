/**
 * Поле гипотезы «страны компаний» в окне гипотезы: переключатель «Компании из других стран» и выбор
 * «Не пишем компаниям из» (решения владельца 20.10.2026).
 *
 * Судья сегмента берёт из текста сайта страну самого бизнеса, а полосу ставит код по этому полю:
 * сегмент называет страну, а на сайте видна только другая — «На проверку» (решает человек; так у
 * каждой новой гипотезы) или «Подходят». Компанию из страны «не пишем» судья отклоняет при любом
 * положении переключателя. Правка поля полосы сама не меняет — их ставит пересуд прогона, по
 * фактам из кэша и без модели.
 *
 * Два поля окна, а не одно: между ними — шаг формы окна (`FIELD_GAP` стопки окна), подпись
 * переключателя — как у полей (`sm`, 500), по образцу переключателей окна стоп-листа. Что значит
 * пустой список — пояснением под подписью, как у имени и описания в этом окне, а не подсказкой в
 * поле: подсказка в поле — бледные чернила (как у выключенного), и слова «никому не отказываем»
 * читались бы хуже нормы (замер 20.10: 3,43 : 1 на свету).
 *
 * **Названия стран — у браузера** (`Intl.DisplayNames` через `regionName`): своего списка названий
 * здесь нет. Коды — с сервера (`GET /sales/countries`, та же таблица, что проверяет правку), и только
 * когда выбор открыли: окно без правки стран лишнего запроса не делает.
 */

import { MultiSelect, SegmentedControl, Stack, Text } from '@mantine/core';
import { useMemo, useState } from 'react';

import { regionName } from '../api/regions';
import { HintLabel } from '../components/HintLabel';
import { useCountryCodes } from './hypothesisData';
import type { CountriesField, OtherCountries } from './hypothesisData';

const OTHER_LABEL = 'Компании из других стран';
const EXCLUDED_LABEL = 'Не пишем компаниям из';
const EXCLUDED_NOTE = 'Пусто — никому не отказываем по стране';
const OTHER_HINT =
  'Сегмент называет страну, а на сайте видна только другая. «На проверку» — такой сайт решает человек, «Подходят» — судья принимает его сам. Поправили — пересудите прогон: полосы встанут заново по уже собранным фактам, без модели.';
const EXCLUDED_HINT =
  'Компанию из этих стран судья отклоняет при любом положении переключателя. Страну бизнеса он берёт из текста сайта — адрес, телефон, валюта, — а не видна — из доменной зоны.';
const OTHER_CHOICES: { value: OtherCountries; label: string }[] = [
  { value: 'review', label: 'На проверку' },
  { value: 'accept', label: 'Подходят' },
];

/** Страна строкой выбора: название браузера и код; названия нет — один код. */
export function countryLabel(code: string): string {
  const name = regionName(code);
  return name === undefined ? code : `${name} · ${code}`;
}

/** Варианты выбора: коды сервера и уже выбранные (их видно и до того, как список пришёл). */
function choicesOf(codes: string[] | undefined, chosen: string[]) {
  const all = Array.from(new Set([...(codes ?? []), ...chosen]));
  return all
    .map((code) => ({ value: code, label: countryLabel(code) }))
    .sort((a, b) => a.label.localeCompare(b.label, 'ru'));
}

interface Props {
  field: CountriesField;
  onChange: (field: CountriesField) => void;
}

export function HypothesisCountries({ field, onChange }: Props) {
  const [opened, setOpened] = useState(false);
  const codes = useCountryCodes(opened);
  const chosen = field.excluded_countries;
  const choices = useMemo(() => choicesOf(codes.data?.codes, chosen), [codes.data, chosen]);
  return (
    <>
      <Stack gap={4}>
        <Text component="div" size="sm" fw={500}>
          <HintLabel label={OTHER_LABEL} hint={OTHER_HINT} />
        </Text>
        <SegmentedControl
          aria-label={OTHER_LABEL}
          value={field.other_countries}
          data={OTHER_CHOICES}
          onChange={(picked) =>
            onChange({ ...field, other_countries: picked === 'accept' ? 'accept' : 'review' })
          }
          style={{ alignSelf: 'flex-start' }}
        />
      </Stack>
      <MultiSelect
        label={<HintLabel label={EXCLUDED_LABEL} hint={EXCLUDED_HINT} />}
        labelProps={{ labelElement: 'div' }}
        aria-label={EXCLUDED_LABEL}
        description={EXCLUDED_NOTE}
        data={choices}
        value={chosen}
        onChange={(picked) => onChange({ ...field, excluded_countries: [...picked].sort() })}
        onDropdownOpen={() => setOpened(true)}
        searchable
        clearable
        nothingFoundMessage={codes.isFetching ? 'Загружаем страны…' : 'Такой страны нет'}
      />
    </>
  );
}
