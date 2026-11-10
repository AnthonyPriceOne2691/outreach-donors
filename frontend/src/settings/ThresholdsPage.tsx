/**
 * Пороги отбора со сравнением против действующих.
 *
 * **Порог двигают, глядя на сравнение, а не на число.** «DR не ниже 25» само по
 * себе не говорит ничего; «эти пороги отсекут сверх действующих 8 доменов базы, из
 * них 0 с полученной ценой» — говорит всё. Поэтому сравнение считается на каждое
 * изменение, ещё до сохранения, и показывает обе стороны: и кого отсекут, и кого
 * пропустят (`ThresholdsComparison`).
 *
 * **Сохранение заводит новую версию, а не правит старую.** Смена порога
 * не должна переписывать вердикты прошлых прогонов — иначе через полгода
 * непонятно, почему домен отсеялся. Новой версией судит следующий прогон, а
 * вердикты в базе остаются как есть — и экран этого не обещает (проверка прода
 * 10.10.2026: карточка звалась «Что станет с базой»). История версий с автором
 * и датой лежит здесь же.
 *
 * **Текст трёх карточек начинается с одной кромки** — 32 px от края стекла
 * (аудит 25.09.2026: было 32, 20 и 26).
 *
 * **Поле — по своему числу, границы — у значка «i», отказ — до нажатия**
 * (замечание 28.09.2026: «поля порогов уже, они огромные; валидация на
 * допустимые значения; значки подсказок, в каких диапазонах»). Поле шириной
 * в четверть панели держало восьмизначное число; границы приходят с сервера
 * и проверяют поле тем же правилом, что схема сервера (`thresholdDraft.ts`),
 * — пока порог за границей, сравнение не считается и сохранять нечего.
 *
 * **Правило региона и срок метрик — та же версия** (сверка ТЗ 11.10.2026: «все пороги —
 * в настройках прогона»). Поля под порогами (`RunRulesFields`), сохраняются той же
 * кнопкой; несогласованное правило — отказ под полями, сохранять нечего. Сравнение
 * с действующими их не касается: оно о четырёх порогах и метриках базы.
 *
 * **Сравнение — только у изменённых порогов, история — последними версиями** (разведка
 * 10.11.2026). Карточка сравнения стояла всегда ради фразы «пороги совпадают»; теперь
 * она появляется с правкой, а до неё под кнопкой — тихая строка, почему сохранять нечего.
 * История растёт с каждой версией: на виду действующая и ещё три, остальные — «Показать
 * ещё N», и «Свернуть» — та же кнопка.
 */

import { Alert, Badge, Button, Card, Group, Loader, Stack, Table, Text } from '@mantine/core';
import { useDebouncedValue } from '@mantine/hooks';
import { keepPreviousData, useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useEffect, useId, useMemo, useState } from 'react';

import { refusalOf } from '../api/client';
import { fetchThresholds, previewThresholds, saveThresholds } from '../api/settings';
import type { RulesVersion, RunRules, ThresholdsFields, VersionBody } from '../api/settings';
import { ColumnsHead } from '../components/ColumnsHead';
import type { Column } from '../components/ColumnsHead';
import { PageHead } from '../components/PageHead';
import { SaveVersionButton } from '../components/SaveVersionButton';
import { Seams } from '../components/Seams';
import { useSession } from '../auth/AuthProvider';
import { formatDateTime, formatNumber } from '../format';
import { LimitField } from './LimitField';
import { coverageRefusal, rulesDraftOf, rulesOf, sameRules } from './rulesDraft';
import type { RulesDraft } from './rulesDraft';
import { RunRulesFields } from './RunRulesFields';
import { bodyOf, draftOf, firstRefused, THRESHOLD_KEYS } from './thresholdDraft';
import type { ThresholdDraft, ThresholdKey } from './thresholdDraft';
import { ThresholdsComparison } from './ThresholdsComparison';
import { AdvertiserScoring } from './AdvertiserScoring';
import { notify } from '../notices';

const THRESHOLDS_KEY = ['thresholds'] as const;

/** Поля порогов: подпись и что порог значит — в подсказке «i» рядом с границами.
 *
 *  До 09.10.2026 смысл стоял строкой под полем, и в ней были вшитые доли — «убирает
 *  19% доменов», «убирает ещё 25%»: замер одного дня, который не пересчитывался ни от
 *  порога, ни от базы и при другом пороге врал (аудит экранов 09.10.2026). Как новые
 *  пороги разойдутся с действующими, говорит карточка ниже — числом с сервера. */
const FIELDS: Record<ThresholdKey, { label: string; hint: string }> = {
  min_dr: { label: 'DR не ниже', hint: 'Первая ступень отбора: 2 юнита Ahrefs на домен.' },
  min_org_traffic: {
    label: 'Органический трафик',
    hint: 'Органический трафик сайта в месяц по Ahrefs.',
  },
  min_refdomains: { label: 'Реф. доменов', hint: 'Сколько сайтов ссылаются на донора.' },
  min_keywords: { label: 'Ключей в органике', hint: 'Сколько ключей сайта в органической выдаче.' },
  max_dr: {
    label: 'Крупный сайт, DR выше',
    hint: 'DR выше этого — крупный сайт: «не подходит», ему не пишем ни письмом, ни формой.',
  },
};

/** Колонки истории. Первая — номер со значком «действует» («№999 действует» — 138 px
 *  шрифтом экрана, замер 10.11.2026), «Регион · срок» — «топ-5 или 100% · 365 дн.»,
 *  169 px; числа — «10 000 000» 75 px, заголовки «Реф. домены» и «Крупный, DR >» — 93 и
 *  104 px. Плюс 32 px полей ячейки. Последняя — адрес автора по швам и время второй
 *  строкой («10.10.2026, 20:48» — 117 px).
 *  **Ширина задана у всех**, и запас панели делится между ними пропорционально: до
 *  09.10.2026 «Кто и когда» была остатком и забирала ~570 px, числа жались к левой
 *  половине, а между «Ключи» и датой зиял провал (аудит экранов 09.10.2026).
 *  **Сумма — не шире карточки на 1440 с открытым меню** (1 101 px): до 10.11.2026 колонки
 *  давали 1 192 px, и «Кто и когда» срезалась краем карточки во всех строках. */
const COLUMNS: Column[] = [
  { title: 'Версия', width: '10rem' },
  { title: 'DR', width: '5rem' },
  { title: 'Трафик', width: '7rem' },
  { title: 'Реф. домены', width: '8rem' },
  { title: 'Ключи', width: '6.5rem' },
  { title: 'Крупный, DR >', width: '8rem' },
  { title: 'Регион · срок', width: '11rem' },
  { title: 'Кто и когда', width: '12.5rem' },
];
/** Сумма колонок: 68rem. */
const TABLE_MIN_WIDTH = 1088;
/** Версий на виду: действующая и ещё три; остальные — по «Показать ещё». */
const VERSIONS_SHOWN = 4;

/** Правило версии строкой истории: «топ-5 или 20% · 90 дн.». */
function rulesText(rules: RunRules): string {
  return `топ-${rules.geo_top_n} или ${rules.geo_min_share_percent}% · ${rules.metrics_ttl_days} дн.`;
}

function same(left: ThresholdsFields, right: ThresholdsFields): boolean {
  return THRESHOLD_KEYS.every((key) => left[key] === right[key]);
}

/** Строка истории: номер со значком «действует», пороги, правило, кто и когда. Автор — адресом
 *  по швам (после «@» и перед точкой), время — второй строкой: в одну строку адрес с датой не
 *  входил в колонку и резался. Автор неизвестен у версий, заведённых до учёток, — тогда только
 *  время, без слова «неизвестно» в каждой строке. */
function VersionRow({ version, current }: { version: RulesVersion; current: boolean }) {
  return (
    <Table.Tr>
      <Table.Td>
        <Group gap="xs" wrap="nowrap">
          <Text size="sm" fw={500}>
            №{version.version}
          </Text>
          {current && (
            <Badge variant="light" color="green">
              действует
            </Badge>
          )}
        </Group>
      </Table.Td>
      <Table.Td>{formatNumber(version.min_dr)}</Table.Td>
      <Table.Td>{formatNumber(version.min_org_traffic)}</Table.Td>
      <Table.Td>{formatNumber(version.min_refdomains)}</Table.Td>
      <Table.Td>{formatNumber(version.min_keywords)}</Table.Td>
      <Table.Td>{formatNumber(version.max_dr)}</Table.Td>
      <Table.Td>{rulesText(version)}</Table.Td>
      <Table.Td className="wrapCell">
        {version.created_by !== null && (
          <Text size="sm" c="dimmed" className="cellName">
            <Seams text={version.created_by} address />
          </Text>
        )}
        <Text size="sm" c="dimmed">
          {formatDateTime(version.created_at)}
        </Text>
      </Table.Td>
    </Table.Tr>
  );
}

/** История версий: последние — на виду, остальные — по «Показать ещё N», и «Свернуть» — та же
 *  кнопка под таблицей. Скрытые строки не отрисованы. */
function History({ history, current }: { history: RulesVersion[]; current: number | null }) {
  const [all, setAll] = useState(false);
  const restId = useId();
  const shown = history.slice(0, VERSIONS_SHOWN);
  const rest = history.slice(VERSIONS_SHOWN);
  const row = (version: RulesVersion) => (
    <VersionRow key={version.version} version={version} current={version.version === current} />
  );
  return (
    <>
      <Table.ScrollContainer minWidth={TABLE_MIN_WIDTH} type="native" className="scrollSlim">
        <Table
          className="dataTable fixedTable"
          layout="fixed"
          tabularNums
          verticalSpacing="sm"
          horizontalSpacing="md"
        >
          <ColumnsHead columns={COLUMNS} />
          <Table.Tbody>{shown.map(row)}</Table.Tbody>
          {all && <Table.Tbody id={restId}>{rest.map(row)}</Table.Tbody>}
        </Table>
      </Table.ScrollContainer>
      {rest.length > 0 && (
        <Group px="md" pt="xs">
          <Button
            variant="subtle"
            size="compact-sm"
            px={6}
            ml={-6}
            className="press"
            aria-expanded={all}
            aria-controls={restId}
            onClick={() => setAll((was) => !was)}
          >
            {all ? 'Свернуть' : `Показать ещё\u00a0${formatNumber(rest.length)}`}
          </Button>
        </Group>
      )}
    </>
  );
}

/** Экран «Пороги»: пороги отбора — Этап 1, веса кандидатов — Этап 2. Без «Доноров» запроса порогов
 *  нет вовсе: сервер отказал бы, а веса роли «Рекламодатели» нужны. Без обоих прав пункта в меню нет
 *  (`layout/sections.ts`), а по прямому адресу — отказ словами, а не пустой экран с ошибкой весов. */
export function ThresholdsPage() {
  const { can } = useSession();
  if (can('donors')) return <DonorThresholds />;
  if (!can('advertisers')) {
    return (
      <Alert color="yellow" title="Раздел недоступен" m="md">
        Пороги есть у доноров и рекламодателей: этой учётке эти разделы не выданы.
      </Alert>
    );
  }
  return (
    <Stack gap="lg">
      <Card className="glassPanel" px="xl" py="lg">
        <PageHead title="Пороги" />
      </Card>
      <AdvertiserScoring />
    </Stack>
  );
}

function DonorThresholds() {
  const queryClient = useQueryClient();
  const { can } = useSession();
  const canEdit = can('settings');

  const { data, error } = useQuery({
    queryKey: THRESHOLDS_KEY,
    queryFn: fetchThresholds,
  });

  const [draft, setDraft] = useState<ThresholdDraft | null>(null);
  const [rulesDraft, setRulesDraft] = useState<RulesDraft | null>(null);
  // На сервер — только черновик в границах: за границей предпросмотр отказал
  // бы, а экран показывал бы прежние числа как ответ на новый порог.
  // Один и тот же объект, пока черновик не менялся: пауза набора перезапускает
  // свой таймер на каждый новый объект, и собранный заново на каждой
  // отрисовке черновик перерисовывал бы экран каждые 400 мс без конца.
  const body = useMemo(
    () => (draft !== null && data !== undefined ? bodyOf(draft, data.limits) : null),
    [draft, data],
  );
  const [debounced] = useDebouncedValue(body, 400);

  // Черновик заводится от того, что действует сейчас: пороги правят
  // от текущих, а не с чистого листа.
  useEffect(() => {
    if (data !== undefined && draft === null) {
      setDraft(draftOf(data.current ?? data.defaults));
    }
    if (data !== undefined && rulesDraft === null) {
      setRulesDraft(rulesDraftOf(data.current ?? data.defaults));
    }
  }, [data, draft, rulesDraft]);

  // Пороги, которые действуют сейчас, — от них правят и с ними сравнивают.
  const inUse = data === undefined ? undefined : (data.current ?? data.defaults);
  // Сравнение считается по всей базе и нужно только изменённым порогам: у
  // совпадающих с действующими перемен ноль тем же правилом, и экран не спрашивает.
  // До 28.09.2026 каждое открытие экрана пересчитывало базу впустую.
  const debouncedChanged = debounced !== null && inUse !== undefined && !same(debounced, inUse);
  const preview = useQuery({
    queryKey: ['thresholds-preview', debounced],
    queryFn: () => previewThresholds(debounced as ThresholdsFields),
    enabled: debouncedChanged && canEdit,
    placeholderData: keepPreviousData,
  });

  const save = useMutation({
    mutationFn: (sent: VersionBody) => saveThresholds(sent),
    onSuccess: async (version) => {
      await queryClient.invalidateQueries({ queryKey: THRESHOLDS_KEY });
      notify({
        message: `Пороги сохранены как версия ${version.version}. Прошлые вердикты не переписаны.`,
        color: 'green',
      });
    },
    onError: (failure) =>
      notify({ title: 'Не сохранили', message: refusalOf(failure), color: 'red' }),
  });

  // Отказ — раньше ожидания: черновик заводится от ответа, и без ответа
  // экран так и крутил бы значок загрузки вместо причины.
  if (error) {
    return (
      <Alert color="red" title="Пороги не загрузились" m="md">
        {refusalOf(error)}
      </Alert>
    );
  }
  if (data === undefined || draft === null || rulesDraft === null) {
    return <Loader aria-label="Загружаем пороги" m="md" />;
  }

  // Поле с отказом — уже не действующие пороги, даже если цифры в нём те же:
  // «20.0» в поле DR — не 20, а повод для отказа.
  const changed = inUse !== undefined && (body === null || !same(body, inUse));
  // Черновика для сервера нет ровно тогда, когда есть поле с отказом (`bodyOf`).
  const refused = firstRefused(draft, data.limits);
  // Правило региона и срок: годное и согласованное — или сохранять нечего.
  const rules = rulesOf(rulesDraft, data.rule_limits);
  const rulesReady = rules !== null && coverageRefusal(rules) === null;
  const rulesChanged = inUse !== undefined && (rules === null || !sameRules(rules, inUse));

  return (
    <Stack gap="lg">
      <Card className="glassPanel" p="xl">
        <Stack gap="md">
          <PageHead
            title="Пороги отбора"
            hint="Сохранение заводит новую версию, а не правит старую. Ею прогоны судят домены, которые измеряют после, — новые и те, у кого истёк срок метрик. Вердикты, что уже стоят в базе, не переписываются: «Отбор», «Доноры», «Обзор» и письма берут их как есть. Ниже — эти пороги против действующих по метрикам доменов базы, тем же правилом, что у прогона; регион и решения человека там не считаются."
          />

          <Group gap="md" align="flex-start" className="fieldRow">
            {THRESHOLD_KEYS.map((key) => (
              <LimitField
                key={key}
                {...FIELDS[key]}
                range={data.limits[key]}
                value={draft[key]}
                canEdit={canEdit}
                onChange={(text) => setDraft({ ...draft, [key]: text })}
              />
            ))}
          </Group>

          <RunRulesFields
            draft={rulesDraft}
            limits={data.rule_limits}
            canEdit={canEdit}
            onChange={setRulesDraft}
          />

          {!canEdit && (
            <Alert color="yellow" title="Править пороги не разрешено">
              Смотреть текущие и историю можно, правка выдана отдельным правом.
            </Alert>
          )}

          <Group>
            <SaveVersionButton
              busy={save.isPending}
              disabled={!canEdit || !(changed || rulesChanged) || body === null || !rulesReady}
              onSave={() => body !== null && rules !== null && save.mutate({ ...body, ...rules })}
            />
            {(changed || rulesChanged) && (
              <Button
                variant="subtle"
                className="press"
                onClick={() => {
                  setDraft(draftOf(inUse));
                  setRulesDraft(rulesDraftOf(inUse));
                }}
              >
                Вернуть действующие
              </Button>
            )}
          </Group>
          {/* Почему «Сохранить» заперта и сравнения нет — тихой строкой под кнопкой, а не
              карточкой ради одной фразы: сравнение встаёт ниже с первой правкой порога. */}
          {canEdit && !changed && !rulesChanged && (
            <Text size="sm" c="dimmed" className="thresholdsSame">
              Пороги совпадают с действующими — сохранять и сравнивать нечего.
            </Text>
          )}
        </Stack>
      </Card>

      {canEdit && changed && (
        <ThresholdsComparison
          refused={refused === null ? null : FIELDS[refused].label}
          data={preview.data}
          fetching={preview.isFetching}
          error={preview.error}
        />
      )}

      {/* Поля карточки с таблицей — вместе с полем ячейки те же 32 px, что
          у карточек выше. */}
      <Card className="glass" p="md">
        <History history={data.history} current={data.current?.version ?? null} />
        {data.history.length === 0 && (
          <Text size="sm" c="dimmed" p="md">
            Версий ещё нет — действуют умолчания. Первая появится здесь после сохранения, вместе с
            автором и датой.
          </Text>
        )}
      </Card>

      {can('advertisers') && <AdvertiserScoring />}
    </Stack>
  );
}
