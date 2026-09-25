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
import { IconDeviceFloppy } from '@tabler/icons-react';
import { keepPreviousData, useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useEffect, useState } from 'react';

import { refusalOf } from '../api/client';
import { fetchThresholds, previewThresholds, saveThresholds } from '../api/settings';
import type { ThresholdsBody } from '../api/types';
import { Metric } from '../components/Metric';
import { useSession } from '../auth/AuthProvider';
import { formatDateTime, formatNumber } from '../format';

const THRESHOLDS_KEY = ['thresholds'] as const;

/** Колонки истории. Первая — номер со значком «действует» (124 px шрифтом
 *  экрана, замер 25.09.2026), последняя — остаток: в ней адрес автора и
 *  время. Числа — по шесть знаков с разрядами, заголовок «Реф. домены» —
 *  85 px. Плюс 32 px полей ячейки. */
const COLUMNS: { title: string; width?: string }[] = [
  { title: 'Версия', width: '11rem' },
  { title: 'DR', width: '5rem' },
  { title: 'Трафик', width: '7.5rem' },
  { title: 'Реф. домены', width: '8rem' },
  { title: 'Ключи', width: '7rem' },
  { title: 'Кто и когда' },
];
const TABLE_MIN_WIDTH = 880;

function same(a: ThresholdsBody, b: ThresholdsBody): boolean {
  return (
    a.min_dr === b.min_dr &&
    a.min_org_traffic === b.min_org_traffic &&
    a.min_refdomains === b.min_refdomains &&
    a.min_keywords === b.min_keywords
  );
}

export function ThresholdsPage() {
  const queryClient = useQueryClient();
  const { can } = useSession();
  const canEdit = can('settings');

  const { data, isLoading, error } = useQuery({
    queryKey: THRESHOLDS_KEY,
    queryFn: fetchThresholds,
  });

  const [draft, setDraft] = useState<ThresholdsBody | null>(null);
  const [debounced] = useDebouncedValue(draft, 400);

  // Черновик заводится от того, что действует сейчас: пороги правят
  // от текущих, а не с чистого листа.
  useEffect(() => {
    if (data !== undefined && draft === null) {
      setDraft(data.current ?? data.defaults);
    }
  }, [data, draft]);

  const preview = useQuery({
    queryKey: ['thresholds-preview', debounced],
    queryFn: () => previewThresholds(debounced as ThresholdsBody),
    enabled: debounced !== null && canEdit,
    placeholderData: keepPreviousData,
  });

  const save = useMutation({
    mutationFn: () => saveThresholds(draft as ThresholdsBody),
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

  if (isLoading || draft === null) return <Loader aria-label="Загружаем пороги" m="md" />;
  if (error) {
    return (
      <Alert color="red" title="Пороги не загрузились" m="md">
        {refusalOf(error)}
      </Alert>
    );
  }

  const inUse = data?.current ?? data?.defaults;
  const changed = inUse !== undefined && !same(draft, inUse);
  const set = (key: keyof ThresholdsBody) => (value: string | number) => {
    setDraft({ ...draft, [key]: typeof value === 'number' ? value : Number(value) || 0 });
  };

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

          <SimpleGrid cols={{ base: 2, md: 4 }} spacing="md" className="fieldRow">
            <NumberInput
              label="DR не ниже"
              description="первая ступень отбора, 2 юнита на домен"
              min={0}
              max={90}
              disabled={!canEdit}
              value={draft.min_dr}
              onChange={set('min_dr')}
            />
            <NumberInput
              label="Органический трафик"
              description="убирает 19% доменов"
              min={0}
              disabled={!canEdit}
              value={draft.min_org_traffic}
              onChange={set('min_org_traffic')}
            />
            <NumberInput
              label="Реф. доменов"
              description="поверх двух других почти не отсекает"
              min={0}
              disabled={!canEdit}
              value={draft.min_refdomains}
              onChange={set('min_refdomains')}
            />
            <NumberInput
              label="Ключей в органике"
              description="убирает ещё 25%"
              min={0}
              disabled={!canEdit}
              value={draft.min_keywords}
              onChange={set('min_keywords')}
            />
          </SimpleGrid>

          {!canEdit && (
            <Alert color="yellow" title="Править пороги не разрешено">
              Смотреть текущие и историю можно, правка выдана отдельным правом.
            </Alert>
          )}

          <Group>
            <Button
              className="press"
              variant="gradient"
              leftSection={<IconDeviceFloppy size={18} />}
              loading={save.isPending}
              disabled={!canEdit || !changed}
              onClick={() => save.mutate()}
            >
              Сохранить новой версией
            </Button>
            {changed && (
              <Button variant="subtle" className="press" onClick={() => setDraft(inUse ?? draft)}>
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
              className="staleable"
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
              {(data?.history ?? []).map((version) => (
                <Table.Tr key={version.version}>
                  <Table.Td>
                    <Group gap="xs" wrap="nowrap">
                      <Text fw={500}>№{version.version}</Text>
                      {version.version === data?.current?.version && (
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
        {(data?.history ?? []).length === 0 && (
          <Text size="sm" c="dimmed" p="md">
            Версий ещё нет — действуют умолчания. Первая появится здесь после сохранения, вместе с
            автором и датой.
          </Text>
        )}
      </Card>
    </Stack>
  );
}
