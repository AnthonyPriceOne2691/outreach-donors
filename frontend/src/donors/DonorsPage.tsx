/**
 * База доноров: таблица со статусом, причиной отсева и фильтрами.
 *
 * **«Не проверен» и «не подходит» — разные состояния**, и в таблице они
 * разного цвета. Спутать их значит копить ложные отказы: домен без данных
 * Ahrefs надо добрать позже, а не закрыть.
 *
 * **Причина отсева показывается всегда.** «Не подходит» без причины —
 * это решение, которое нельзя оспорить, а пороги у нас версионируются
 * именно затем, чтобы прошлые решения объяснялись.
 */

import {
  Alert,
  Badge,
  Button,
  Card,
  Group,
  Loader,
  NumberInput,
  Pagination,
  Stack,
  Switch,
  Table,
  Text,
  TextInput,
  Title,
} from '@mantine/core';
import { useQuery } from '@tanstack/react-query';
import { useState } from 'react';
import { useNavigate } from 'react-router-dom';

import { CONTACT_STATUSES, DONOR_STATUSES } from '../api/labels';
import { listDonors } from '../api/runs';
import type { DonorStatus } from '../api/types';
import { Contacts } from './Contacts';

const PAGE_SIZE = 50;

function refusalOf(error: unknown): string {
  return error instanceof Error ? error.message : 'Сервер отказал без объяснения';
}

function traffic(value: number | null): string {
  if (value === null) return '—';
  if (value >= 1_000_000) return `${(value / 1_000_000).toFixed(1)} млн`;
  if (value >= 1_000) return `${(value / 1_000).toFixed(0)} тыс.`;
  return String(value);
}

export function DonorsPage() {
  const navigate = useNavigate();
  const [status, setStatus] = useState<DonorStatus | null>(null);
  const [search, setSearch] = useState('');
  const [minDr, setMinDr] = useState<number | null>(null);
  const [onlyWithContact, setOnlyWithContact] = useState(false);
  const [page, setPage] = useState(1);

  const query = useQuery({
    queryKey: ['donors', status, search, minDr, onlyWithContact, page],
    queryFn: () =>
      listDonors({
        ...(status !== null ? { status } : {}),
        ...(search.trim() !== '' ? { search: search.trim() } : {}),
        ...(minDr !== null ? { min_dr: minDr } : {}),
        ...(onlyWithContact ? { has_contact: true } : {}),
        limit: PAGE_SIZE,
        offset: (page - 1) * PAGE_SIZE,
      }),
  });

  if (query.isLoading) return <Loader aria-label="Загружаем доноров" m="md" />;
  if (query.error) {
    return (
      <Alert color="red" title="База не загрузилась" m="md">
        {refusalOf(query.error)}
      </Alert>
    );
  }

  const data = query.data;
  const rows = data?.rows ?? [];
  const counts = data?.counts ?? {};
  const pages = Math.max(1, Math.ceil((data?.total ?? 0) / PAGE_SIZE));

  const pick = (value: DonorStatus | null) => {
    setStatus(value);
    setPage(1);
  };

  const filters = new URLSearchParams({
    ...(status !== null ? { status } : {}),
    ...(search.trim() !== '' ? { search: search.trim() } : {}),
    ...(minDr !== null ? { min_dr: String(minDr) } : {}),
    ...(onlyWithContact ? { has_contact: 'true' } : {}),
  });

  return (
    <Stack gap="lg">
      <Contacts />

      <Card className="glassPanel" p="xl">
        <Stack gap="md">
          <Group justify="space-between" align="flex-start">
            <Stack gap={6}>
              <Title order={3}>Доноры</Title>
              <Text size="sm" c="dimmed" maw={620}>
                «Не проверен» — не «не подходит»: у домена не было данных, и его надо добрать позже.
                Причина отсева показана рядом со статусом.
              </Text>
            </Stack>
            <Group gap="sm" align="flex-end">
              <TextInput
                placeholder="Домен или причина отсева"
                aria-label="Поиск по домену или причине отсева"
                w={280}
                value={search}
                onChange={(event) => {
                  setSearch(event.currentTarget.value);
                  setPage(1);
                }}
              />
              {/* Выгружается то, что видно: фильтр — часть вопроса,
                  на который отвечают файлом. Выгрузка «всего» при
                  включённом фильтре не совпала бы с экраном. */}
              <Button
                component="a"
                href={`/api/donors/export?${filters.toString()}`}
                variant="default"
                className="press"
              >
                Выгрузить
              </Button>
            </Group>
          </Group>

          <Group gap="xs">
            <Badge
              variant={status === null ? 'filled' : 'light'}
              color="lagoon"
              className="press"
              style={{ cursor: 'pointer' }}
              onClick={() => pick(null)}
            >
              все — {Object.values(counts).reduce((sum, count) => sum + count, 0)}
            </Badge>
            {Object.entries(counts).map(([value, count]) => (
              <Badge
                key={value}
                variant={status === value ? 'filled' : 'light'}
                color={DONOR_STATUSES[value as DonorStatus].color}
                className="press"
                style={{ cursor: 'pointer' }}
                onClick={() => pick(status === value ? null : (value as DonorStatus))}
              >
                {DONOR_STATUSES[value as DonorStatus].title} — {count}
              </Badge>
            ))}
          </Group>

          <Group gap="lg">
            <NumberInput
              label="DR не ниже"
              w={140}
              min={0}
              max={100}
              value={minDr ?? ''}
              onChange={(value) => {
                setMinDr(typeof value === 'number' ? value : null);
                setPage(1);
              }}
            />
            <Switch
              label="Только с найденным адресом"
              checked={onlyWithContact}
              onChange={(event) => {
                setOnlyWithContact(event.currentTarget.checked);
                setPage(1);
              }}
            />
          </Group>
        </Stack>
      </Card>

      <Card className="glass" p="xs">
        {rows.length === 0 ? (
          <Text size="sm" c="dimmed" p="lg">
            Под фильтр ничего не попало. Это не пустая база: всего доноров{' '}
            {Object.values(counts).reduce((sum, count) => sum + count, 0)}.
          </Text>
        ) : (
          <Table className="dataTable" verticalSpacing="sm" horizontalSpacing="md" miw={900}>
            <Table.Thead>
              <Table.Tr>
                <Table.Th>Донор</Table.Th>
                <Table.Th>Вердикт</Table.Th>
                <Table.Th>DR</Table.Th>
                <Table.Th>Трафик</Table.Th>
                <Table.Th>Гео</Table.Th>
                <Table.Th>Адреса</Table.Th>
                <Table.Th>Данные</Table.Th>
              </Table.Tr>
            </Table.Thead>
            <Table.Tbody>
              {rows.map((donor) => (
                <Table.Tr
                  key={donor.id}
                  style={{ cursor: 'pointer' }}
                  onClick={() => void navigate(`/donors/${donor.id}`)}
                >
                  <Table.Td>
                    <Text fw={500}>{donor.host}</Text>
                    {donor.reject_reason !== null && (
                      <Text size="xs" c="dimmed">
                        {donor.reject_reason}
                      </Text>
                    )}
                  </Table.Td>
                  <Table.Td>
                    <Group justify="center">
                      <Badge variant="light" color={DONOR_STATUSES[donor.status].color}>
                        {DONOR_STATUSES[donor.status].title}
                      </Badge>
                    </Group>
                  </Table.Td>
                  <Table.Td>{donor.dr ?? '—'}</Table.Td>
                  <Table.Td>{traffic(donor.org_traffic)}</Table.Td>
                  <Table.Td>
                    {donor.geo === null
                      ? '—'
                      : `${donor.geo} ${
                          donor.geo_top_share === null
                            ? ''
                            : `· ${(donor.geo_top_share * 100).toFixed(0)}%`
                        }`}
                  </Table.Td>
                  <Table.Td>
                    <Group justify="center" gap={6}>
                      <Text size="sm">{donor.contacts}</Text>
                      {donor.contact_status !== null && (
                        <Badge
                          variant="light"
                          size="sm"
                          color={CONTACT_STATUSES[donor.contact_status].color}
                        >
                          {CONTACT_STATUSES[donor.contact_status].title}
                        </Badge>
                      )}
                    </Group>
                  </Table.Td>
                  <Table.Td>
                    {/* Свежесть — это про деньги: за свежие данные второй раз
                        не платят, поэтому она видна в таблице, а не в карточке. */}
                    <Badge variant="light" color={donor.fresh ? 'green' : 'gray'}>
                      {donor.fresh ? 'в сроке' : 'пора обновить'}
                    </Badge>
                  </Table.Td>
                </Table.Tr>
              ))}
            </Table.Tbody>
          </Table>
        )}
      </Card>

      {pages > 1 && (
        <Group justify="center">
          <Pagination value={page} onChange={setPage} total={pages} radius="xl" />
        </Group>
      )}
    </Stack>
  );
}
