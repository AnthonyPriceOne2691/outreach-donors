/**
 * Пороги отбора с предпросмотром последствий.
 *
 * **Порог двигают, глядя на последствия, а не на число.** «DR не ниже 25»
 * само по себе не говорит ничего; «из базы выпадет 8 доноров, из них 0
 * с полученной ценой» — говорит всё. Поэтому предпросмотр считается
 * на каждое изменение, ещё до сохранения, и показывает обе стороны:
 * и что выпадет, и что вернётся.
 *
 * **Сохранение заводит новую версию, а не правит старую.** Смена порога
 * не должна переписывать вердикты прошлых прогонов — иначе через полгода
 * непонятно, почему домен отсеялся. История версий с автором и датой
 * лежит здесь же.
 *
 * **Пересчёт не перерисовывает блок последствий.** Пока идёт новый расчёт,
 * прежние числа стоят приглушёнными: значок загрузки на месте блока на
 * каждое изменение порога заставлял экран прыгать под рукой.
 *
 * **Текст трёх карточек начинается с одной кромки** — 32 px от края стекла
 * (аудит 25.09.2026: было 32, 20 и 26).
 *
 * **Поле — по своему числу, границы — у значка «i», отказ — до нажатия**
 * (замечание 28.09.2026: «поля порогов уже, они огромные; валидация на
 * допустимые значения; значки подсказок, в каких диапазонах»). Поле шириной
 * в четверть панели держало восьмизначное число; границы приходят с сервера
 * и проверяют поле тем же правилом, что схема сервера (`thresholdDraft.ts`),
 * — пока порог за границей, последствия не считаются и сохранять нечего.
 */

import {
  Alert,
  Badge,
  Button,
  Card,
  Group,
  Loader,
  NumberInput,
  SimpleGrid,
  Stack,
  Table,
  Text,
  Title,
} from '@mantine/core';
import { useDebouncedValue } from '@mantine/hooks';
import { notifications } from '@mantine/notifications';
import { keepPreviousData, useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useEffect, useMemo, useState } from 'react';

import { refusalOf } from '../api/client';
import { fetchThresholds, previewThresholds, saveThresholds } from '../api/settings';
import type { ThresholdsBody } from '../api/types';
import { InfoHint } from '../components/InfoHint';
import { Metric } from '../components/Metric';
import { SaveVersionButton } from '../components/SaveVersionButton';
import { useSession } from '../auth/AuthProvider';
import { formatDateTime, formatNumber } from '../format';
import { bodyOf, fieldRefusal, rangeText, THRESHOLD_KEYS, typed } from './thresholdDraft';
import type { ThresholdDraft, ThresholdKey } from './thresholdDraft';

const THRESHOLDS_KEY = ['thresholds'] as const;

/** Поля порогов: подпись и что порог значит — в подсказке «i» рядом с границами.
 *
 *  До 09.10.2026 смысл стоял строкой под полем, и в ней были вшитые доли — «убирает
 *  19% доменов», «убирает ещё 25%»: замер одного дня, который не пересчитывался ни от
 *  порога, ни от базы и при другом пороге врал (аудит экранов 09.10.2026). Что станет с
 *  базой при новых порогах, говорит карточка ниже — числом с сервера. */
const FIELDS: Record<ThresholdKey, { label: string; hint: string }> = {
  min_dr: { label: 'DR не ниже', hint: 'Первая ступень отбора: 2 юнита Ahrefs на домен.' },
  min_org_traffic: {
    label: 'Органический трафик',
    hint: 'Органический трафик сайта в месяц по Ahrefs.',
  },
  min_refdomains: { label: 'Реф. доменов', hint: 'Сколько сайтов ссылаются на донора.' },
  min_keywords: { label: 'Ключей в органике', hint: 'Сколько ключей сайта в органической выдаче.' },
};

/** **Поле — по числу**: самому длинному, «10 000 000» со стрелками и полями, хватает
 *  115 px (замер 28.09.2026). До 09.10.2026 поле стояло во всю колонку подписи —
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

function same(draft: ThresholdDraft, inUse: ThresholdsBody): boolean {
  return THRESHOLD_KEYS.every((key) => draft[key] === inUse[key]);
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
      setDraft(data.current ?? data.defaults);
    }
  }, [data, draft]);

  // Пороги, которые действуют сейчас, — от них правят и с ними сравнивают.
  const inUse = data === undefined ? undefined : (data.current ?? data.defaults);
  // Последствия считаются по всей базе и нужны только изменённым порогам:
  // у совпадающих с действующими экран их и не показывает. До 28.09.2026
  // каждое открытие экрана пересчитывало базу впустую.
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
      notifications.show({
        message: `Пороги сохранены как версия ${version.version}. Прошлые вердикты не переписаны.`,
        color: 'green',
      });
    },
    onError: (failure) =>
      notifications.show({ title: 'Не сохранили', message: refusalOf(failure), color: 'red' }),
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

  const changed = inUse !== undefined && !same(draft, inUse);

  return (
    <Stack gap="lg">
      <Card className="glassPanel" p="xl">
        <Stack gap="md">
          <Stack gap={6}>
            <Title order={3}>Пороги отбора</Title>
            <Text size="sm" c="dimmed" maw={680}>
              Сохранение заводит новую версию, а не правит старую: вердикты прошлых прогонов должны
              оставаться объяснимыми. Ниже — что станет с базой, если применить новые пороги.
            </Text>
          </Stack>

          {/* Подпись — не `<label>`: в ней кнопка подсказки, а кнопка внутри
              подписи поля — вложенный элемент формы, и её имя вошло бы в имя
              поля. Имя полю — `aria-label`, словами подписи. */}
          <Group gap="md" align="flex-start" className="fieldRow">
            {THRESHOLD_KEYS.map((key) => {
              const range = data.limits[key];
              const field = FIELDS[key];
              return (
                <NumberInput
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
                  min={range.min}
                  max={range.max}
                  // Число за границей не подменяется молча краем диапазона:
                  // поле остаётся как набрано, под ним — почему так нельзя.
                  clampBehavior="none"
                  allowDecimal={false}
                  allowNegative={false}
                  // На телефоне — клавиатура из одних цифр: запятой и минуса
                  // в пороге не бывает, а по умолчанию поле просит «дробную».
                  inputMode="numeric"
                  thousandSeparator=" "
                  disabled={!canEdit}
                  value={draft[key]}
                  error={canEdit ? fieldRefusal(draft[key], range) : null}
                  onChange={(value) => setDraft({ ...draft, [key]: typed(value) })}
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
              <Button variant="subtle" className="press" onClick={() => setDraft(inUse)}>
                Вернуть действующие
              </Button>
            )}
          </Group>
        </Stack>
      </Card>

      {canEdit && (
        <Card className="glass" p="xl">
          <Title order={5} mb="sm">
            Что станет с базой
          </Title>
          {/* Пока черновик совпадает с действующими, последствий нет по
              определению — и экран не показывает их, даже если пересчёт
              по нынешним правилам дал бы расхождение со старыми вердиктами. */}
          {!changed ? (
            <Text size="sm" c="dimmed">
              Пороги совпадают с действующими — база не изменится. Измените порог, и здесь появится,
              кто выпадет и кто вернётся.
            </Text>
          ) : body === null ? (
            <Text size="sm" c="dimmed">
              Порог вне допустимых границ — поправьте поле с пометкой, и здесь появится, кто выпадет
              и кто вернётся.
            </Text>
          ) : preview.data === undefined ? (
            preview.isFetching ? (
              <Loader size="sm" aria-label="Считаем последствия" />
            ) : (
              <Text size="sm" c="dimmed">
                Последствия считаются по всей базе — измените порог, и они появятся.
              </Text>
            )
          ) : (
            <Stack
              gap="sm"
              className="staleRows"
              data-stale={preview.isFetching || undefined}
              aria-busy={preview.isFetching || undefined}
            >
              <SimpleGrid cols={{ base: 2, md: 4 }} spacing="sm">
                <Metric title="Подходит сейчас" value={formatNumber(preview.data.suitable_now)} />
                <Metric title="Будет подходить" value={formatNumber(preview.data.suitable_after)} />
                <Metric
                  title="Выпадет из базы"
                  value={formatNumber(preview.data.falls_out)}
                  color={preview.data.falls_out > 0 ? 'yellow' : undefined}
                  hint={`из них с ценой: ${preview.data.falls_out_with_price}`}
                />
                <Metric title="Вернётся в базу" value={formatNumber(preview.data.comes_back)} />
              </SimpleGrid>

              {preview.data.falls_out_with_price > 0 && (
                <Alert color="yellow" title="Среди выпавших есть доноры с полученной ценой">
                  За них заплачено не только юнитами, но и письмом. Пороги это не запрещает — просто
                  стоит знать до, а не после.
                </Alert>
              )}
              {preview.data.unchecked > 0 && (
                <Text size="sm" c="dimmed">
                  Ещё {preview.data.unchecked} доменов без метрик: их вердикт не изменится, потому
                  что его нет. Это повод добрать данные, а не отсев.
                </Text>
              )}
            </Stack>
          )}
        </Card>
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
