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
 */

import { Alert, Badge, Button, Card, Group, Loader, Stack, Table, Text } from '@mantine/core';
import { useDebouncedValue } from '@mantine/hooks';
import { keepPreviousData, useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useEffect, useMemo, useState } from 'react';

import { refusalOf } from '../api/client';
import { fetchThresholds, previewThresholds, saveThresholds } from '../api/settings';
import type { ThresholdsBody } from '../api/types';
import { InfoHint } from '../components/InfoHint';
import { NumberField } from '../components/NumberField';
import { rangeText } from '../components/numberText';
import { PageHead } from '../components/PageHead';
import { SaveVersionButton } from '../components/SaveVersionButton';
import { useSession } from '../auth/AuthProvider';
import { formatDateTime, formatNumber } from '../format';
import { bodyOf, draftOf, fieldRefusal, firstRefused, THRESHOLD_KEYS } from './thresholdDraft';
import type { ThresholdDraft, ThresholdKey } from './thresholdDraft';
import { ThresholdsComparison } from './ThresholdsComparison';
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
};

/** **Поле — по числу**: самому длинному, «10 000 000» с полями, хватает 115 px (замер
 *  28.09.2026, тогда ещё со стрелками). До 09.10.2026 поле стояло во всю колонку подписи —
 *  200 px под «20», то самое «поле больше своего значения» (замечание Anthony
 *  09.10.2026). Колонка — по своей подписи: подпись не переносится (`nowrap`), и
 *  соседние поля стоят на одной высоте. */
const INPUT_WIDTH = '7.5rem';

/** Колонки истории. Первая — номер со значком «действует» (124 px шрифтом
 *  экрана, замер 25.09.2026), последняя — адрес автора и время. Числа — по шесть
 *  знаков с разрядами, заголовок «Реф. домены» — 85 px. Плюс 32 px полей ячейки.
 *  **Ширина задана у всех**, и запас панели делится между ними пропорционально: до
 *  09.10.2026 «Кто и когда» была остатком и забирала ~570 px, числа жались к левой
 *  половине, а между «Ключи» и датой зиял провал (аудит экранов 09.10.2026). */
const COLUMNS: { title: string; width?: string }[] = [
  { title: 'Версия', width: '11rem' },
  { title: 'DR', width: '5rem' },
  { title: 'Трафик', width: '7.5rem' },
  { title: 'Реф. домены', width: '8rem' },
  { title: 'Ключи', width: '7rem' },
  { title: 'Кто и когда', width: '17rem' },
];
const TABLE_MIN_WIDTH = 880;

function same(left: ThresholdsBody, right: ThresholdsBody): boolean {
  return THRESHOLD_KEYS.every((key) => left[key] === right[key]);
}

export function ThresholdsPage() {
  const queryClient = useQueryClient();
  const { can } = useSession();
  const canEdit = can('settings');

  const { data, error } = useQuery({
    queryKey: THRESHOLDS_KEY,
    queryFn: fetchThresholds,
  });

  const [draft, setDraft] = useState<ThresholdDraft | null>(null);
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
  }, [data, draft]);

  // Пороги, которые действуют сейчас, — от них правят и с ними сравнивают.
  const inUse = data === undefined ? undefined : (data.current ?? data.defaults);
  // Сравнение считается по всей базе и нужно только изменённым порогам: у
  // совпадающих с действующими перемен ноль тем же правилом, и экран не спрашивает.
  // До 28.09.2026 каждое открытие экрана пересчитывало базу впустую.
  const debouncedChanged = debounced !== null && inUse !== undefined && !same(debounced, inUse);
  const preview = useQuery({
    queryKey: ['thresholds-preview', debounced],
    queryFn: () => previewThresholds(debounced as ThresholdsBody),
    enabled: debouncedChanged && canEdit,
    placeholderData: keepPreviousData,
  });

  const save = useMutation({
    mutationFn: (sent: ThresholdsBody) => saveThresholds(sent),
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
  if (data === undefined || draft === null) {
    return <Loader aria-label="Загружаем пороги" m="md" />;
  }

  // Поле с отказом — уже не действующие пороги, даже если цифры в нём те же:
  // «20.0» в поле DR — не 20, а повод для отказа.
  const changed = inUse !== undefined && (body === null || !same(body, inUse));
  // Черновика для сервера нет ровно тогда, когда есть поле с отказом (`bodyOf`).
  const refused = firstRefused(draft, data.limits);

  return (
    <Stack gap="lg">
      <Card className="glassPanel" p="xl">
        <Stack gap="md">
          <PageHead
            title="Пороги отбора"
            hint="Сохранение заводит новую версию, а не правит старую. Ею прогоны судят домены, которые измеряют после, — новые и те, у кого истёк срок метрик. Вердикты, что уже стоят в базе, не переписываются: «Отбор», «Доноры», «Обзор» и письма берут их как есть. Ниже — эти пороги против действующих по метрикам доменов базы, тем же правилом, что у прогона; регион и решения человека там не считаются."
          />

          {/* Подпись — не `<label>`: в ней кнопка подсказки, а кнопка внутри
              подписи поля — вложенный элемент формы, и её имя вошло бы в имя
              поля. Имя полю — `aria-label`, словами подписи. */}
          <Group gap="md" align="flex-start" className="fieldRow">
            {THRESHOLD_KEYS.map((key) => {
              const range = data.limits[key];
              const field = FIELDS[key];
              return (
                <NumberField
                  key={key}
                  labelProps={{ labelElement: 'div' }}
                  label={
                    <Group component="span" gap={4} wrap="nowrap">
                      {field.label}
                      <InfoHint name={`Допустимые значения: ${field.label}`} width={260}>
                        {field.hint} Допустимо: целое число {rangeText(range)}
                      </InfoHint>
                    </Group>
                  }
                  aria-label={field.label}
                  styles={{ wrapper: { width: INPUT_WIDTH } }}
                  // Ни число за границей, ни дробь, ни буква не подменяются молча:
                  // поле остаётся как набрано, под ним — почему так нельзя.
                  disabled={!canEdit}
                  value={draft[key]}
                  error={canEdit ? fieldRefusal(draft[key], range) : null}
                  onChange={(text) => setDraft({ ...draft, [key]: text })}
                />
              );
            })}
          </Group>

          {!canEdit && (
            <Alert color="yellow" title="Править пороги не разрешено">
              Смотреть текущие и историю можно, правка выдана отдельным правом.
            </Alert>
          )}

          <Group>
            <SaveVersionButton
              busy={save.isPending}
              disabled={!canEdit || !changed || body === null}
              onSave={() => body !== null && save.mutate(body)}
            />
            {changed && (
              <Button variant="subtle" className="press" onClick={() => setDraft(draftOf(inUse))}>
                Вернуть действующие
              </Button>
            )}
          </Group>
        </Stack>
      </Card>

      {canEdit && (
        <ThresholdsComparison
          changed={changed}
          refused={refused === null ? null : FIELDS[refused].label}
          data={preview.data}
          fetching={preview.isFetching}
          error={preview.error}
        />
      )}

      {/* Поля карточки с таблицей — вместе с полем ячейки те же 32 px, что
          у карточек выше. */}
      <Card className="glass" p="md">
        <Table.ScrollContainer minWidth={TABLE_MIN_WIDTH} type="native" className="scrollSlim">
          <Table
            className="dataTable fixedTable"
            layout="fixed"
            tabularNums
            verticalSpacing="sm"
            horizontalSpacing="md"
          >
            <colgroup>
              {COLUMNS.map((column) => (
                <col
                  key={column.title}
                  style={column.width ? { width: column.width } : undefined}
                />
              ))}
            </colgroup>
            <Table.Thead>
              <Table.Tr>
                {COLUMNS.map((column) => (
                  <Table.Th key={column.title}>{column.title}</Table.Th>
                ))}
              </Table.Tr>
            </Table.Thead>
            <Table.Tbody>
              {data.history.map((version) => (
                <Table.Tr key={version.version}>
                  <Table.Td>
                    <Group gap="xs" wrap="nowrap">
                      <Text size="sm" fw={500}>
                        №{version.version}
                      </Text>
                      {version.version === data.current?.version && (
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
                  <Table.Td className="wrapCell">
                    {/* Автор неизвестен у версий, заведённых до учёток, —
                        тогда только дата, без слова «неизвестно» в каждой строке. */}
                    <Text size="sm" c="dimmed" className="cellName">
                      {version.created_by === null
                        ? formatDateTime(version.created_at)
                        : `${version.created_by} · ${formatDateTime(version.created_at)}`}
                    </Text>
                  </Table.Td>
                </Table.Tr>
              ))}
            </Table.Tbody>
          </Table>
        </Table.ScrollContainer>
        {data.history.length === 0 && (
          <Text size="sm" c="dimmed" p="md">
            Версий ещё нет — действуют умолчания. Первая появится здесь после сохранения, вместе с
            автором и датой.
          </Text>
        )}
      </Card>
    </Stack>
  );
}
