/**
 * «Изменить качество сайтов» гипотезы плана: глубина поиска, цель «Найти сайтов», потолок гипотезы,
 * страны компаний и вид сайта.
 *
 * Глубина, цель и потолок — строки плана, страны компаний — поле самой гипотезы, тем же выбором, что
 * в окне гипотезы (`HypothesisCountries`), и той же правкой на сервере, с её журналом; вид сайта —
 * тоже поле гипотезы (`SiteKinds`, решение владельца 16.11). Действует на новые прогоны и суды;
 * собранное правка сама не пересуживает — страны и вид ложатся на него пересудом прогона, как
 * говорят подсказки полей (стенд 20.11: вводная «не пересуживаются» спорила с ними). Уходит только
 * поправленное; ничего не поправили — окно просто закрывается. Глубже при «нужны ключи» — гипотеза
 * снова собирает.
 *
 * **Потолок гипотезы меняется и после сохранения плана** — по смете с запасом: поднятый снимает
 * «кончился бюджет», и гипотеза, вставшая на потолке, снова собирает; под полем — израсходованное
 * словами отказа сервера и сумма, от которой можно (`capFloorWords`); ниже израсходованного сервер
 * отказывает словами. Поле — `PlanNumberField`: точка и запятая не стираются на ходу.
 *
 * **Фокус при открытии — на первое поле**, выбранную ступень глубины, как у двух других окон
 * (стенд 17.11: вставал на крестик закрытия). Mantine ищет в окне `data-autofocus`, а пунктам
 * переключателя своих свойств не передать — метку выбранному пункту ставит ссылка на блок поля при
 * монтировании, раньше, чем окно ставит фокус (оно ставит его кадром позже).
 */

import { NumberInput, SegmentedControl, Stack, Text } from '@mantine/core';
import { useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';

import type { HypothesisCard } from '../api/salesTypes';
import { formatNumber } from '../format';
import { notify } from '../notices';
import { HypothesisCountries } from './HypothesisCountries';
import { countriesOf, HYPOTHESES_QUERY_KEY } from './hypothesisData';
import type { CountriesField } from './hypothesisData';
import { changeQuality, usePlanAction } from './planApi';
import type { PlanQualityBody, PlanRow, SiteKind } from './planApi';
import { PlanDialog } from './PlanDialog';
import { MAX_TARGET } from './PlanMore';
import { capError, capFloorWords, PlanNumberField } from './PlanNumberField';
import { topWords } from './PlanPreviewView';
import { kindsChange, SiteKinds, siteKindsOf } from './SiteKinds';

/** Ступени «Глубины поиска до» книги — страниц выдачи по 10. */
const DEPTHS = [1, 2, 3, 5].map((pages) => ({ value: String(pages), label: topWords(pages) }));

/** Вводная окна — в согласии с подсказками стран и вида сайта: собранное правка сама не
 *  пересуживает, а пересуд прогона кладёт полосы заново по собранным фактам, без модели. */
const INTRO =
  'Действует на новые прогоны и суды. Уже собранные сайты сами не пересуживаются: чтобы применить к ним страны компаний и вид сайта, пересудите прогон — полосы встанут заново по уже собранным фактам, без модели.';

/** Метка `data-autofocus` выбранному пункту переключателя глубины — первому полю окна. */
function focusFirst(node: HTMLElement | null) {
  node?.querySelector('input:checked')?.setAttribute('data-autofocus', '');
}

/** Что поправили — только это и уходит. */
export function qualityChanges(
  row: PlanRow,
  was: CountriesField,
  pages: number,
  target: number | '',
  field: CountriesField,
  cap: number | null,
): PlanQualityBody {
  const body: PlanQualityBody = {};
  if (pages !== row.max_pages) body.max_pages = pages;
  if (typeof target === 'number' && target !== row.target) body.target = target;
  if (cap !== null && cap !== row.cap_usd) body.cap_usd = cap;
  if (field.other_countries !== was.other_countries) body.other_countries = field.other_countries;
  if (field.excluded_countries.join() !== was.excluded_countries.join()) {
    body.excluded_countries = field.excluded_countries;
  }
  return body;
}

interface Props {
  row: PlanRow;
  /** Гипотеза продаж строки — её поле «страны компаний». */
  card: HypothesisCard;
  onClose: () => void;
}

export function PlanQuality({ row, card, onClose }: Props) {
  const was = countriesOf(card);
  const [pages, setPages] = useState(row.max_pages);
  const [target, setTarget] = useState<number | ''>(row.target);
  const [field, setField] = useState<CountriesField>(was);
  const [cap, setCap] = useState<number | null>(row.cap_usd);
  const wasKinds = siteKindsOf(card);
  const [kinds, setKinds] = useState<SiteKind[]>(wasKinds);
  const client = useQueryClient();
  const send = usePlanAction(
    (body: PlanQualityBody) => changeQuality(row.id, body),
    () => {
      // Страны компаний — поле гипотезы: карточка гипотезы перечитывается вместе с планами.
      void client.invalidateQueries({ queryKey: HYPOTHESES_QUERY_KEY });
      notify({ message: `«${row.name}»: качество сайтов сохранено`, color: 'green' });
      onClose();
    },
  );
  const submit = () => {
    const body = {
      ...qualityChanges(row, was, pages, target, field, cap),
      ...kindsChange(wasKinds, kinds),
    };
    if (Object.keys(body).length === 0) onClose();
    else send.mutate(body);
  };
  return (
    <PlanDialog
      title="Качество сайтов"
      name={row.name}
      refused="Качество не сохранено"
      error={send.error}
      busy={send.isPending}
      ready={target !== '' && cap !== null && cap > 0}
      action="Сохранить"
      onSubmit={submit}
      onClose={onClose}
    >
      <Text size="sm" c="dimmed">
        {INTRO}
      </Text>
      <Stack gap={4} ref={focusFirst}>
        <Text size="sm" fw={500} id={`plan-depth-${row.id}`}>
          Глубина поиска до
        </Text>
        <SegmentedControl
          aria-labelledby={`plan-depth-${row.id}`}
          value={String(pages)}
          data={DEPTHS}
          onChange={(picked) => setPages(Number(picked))}
          style={{ alignSelf: 'flex-start' }}
        />
      </Stack>
      <NumberInput
        label="Найти сайтов"
        description={`Прошедших судью. Найдено: ${formatNumber(row.found)}`}
        min={1}
        max={MAX_TARGET}
        allowDecimal={false}
        allowNegative={false}
        thousandSeparator=" "
        value={target}
        onChange={(value) => setTarget(typeof value === 'number' ? value : '')}
      />
      <PlanNumberField
        label="Потолок гипотезы, $"
        description={`${capFloorWords(row.spent_usd)}. Поднятый потолок снимает «кончился бюджет»`}
        min={0}
        decimalScale={2}
        allowNegative={false}
        value={cap}
        onValue={setCap}
        error={capError(cap)}
      />
      <HypothesisCountries field={field} onChange={setField} />
      <SiteKinds value={kinds} onChange={setKinds} />
    </PlanDialog>
  );
}
