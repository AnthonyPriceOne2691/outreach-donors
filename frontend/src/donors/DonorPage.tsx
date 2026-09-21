/**
 * Карточка донора: метрики, срок годности, адреса и ступень, которая
 * их дала.
 *
 * **Срок годности показан датой, а не словом «свежие».** За данные
 * в сроке уже заплачено, и повторный прогон их не трогает — человек,
 * который видит дату, понимает, когда домен снова будет стоить юнитов.
 *
 * **Ступень рядом с адресом** нужна по той же причине: адрес со страницы
 * сайта и адрес из платного сервиса стоили разного, и решение «добирать
 * ли платным» принимают, глядя на это.
 */

import {
  Alert,
  Badge,
  Button,
  Card,
  Group,
  Loader,
  SimpleGrid,
  Stack,
  Table,
  Text,
  Title,
} from '@mantine/core';
import { IconArrowLeft } from '@tabler/icons-react';
import { useQuery } from '@tanstack/react-query';
import { useNavigate, useParams } from 'react-router-dom';

import { CONTACT_SOURCES, CONTACT_STATUSES, DONOR_STATUSES } from '../api/labels';
import { Metric } from '../components/Metric';
import { fetchDonor } from '../api/runs';

function refusalOf(error: unknown): string {
  return error instanceof Error ? error.message : 'Сервер отказал без объяснения';
}

function when(moment: string | null): string {
  return moment === null ? '—' : new Date(moment).toLocaleDateString('ru-RU');
}

export function DonorPage() {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const { data, isLoading, error } = useQuery({
    queryKey: ['donor', id],
    queryFn: () => fetchDonor(Number(id)),
    enabled: id !== undefined,
  });

  if (isLoading) return <Loader aria-label="Загружаем донора" m="md" />;
  if (error) {
    return (
      <Alert color="red" title="Донор не загрузился" m="md">
        {refusalOf(error)}
      </Alert>
    );
  }
  if (data === undefined) return null;

  return (
    <Stack gap="lg" maw={980}>
      <Card className="glassPanel" p="xl">
        <Group justify="space-between" align="flex-start">
          <Stack gap={6}>
            <Group gap="sm">
              <Title order={3}>{data.host}</Title>
              <Badge variant="light" color={DONOR_STATUSES[data.status].color}>
                {DONOR_STATUSES[data.status].title}
              </Badge>
              {data.contact_status !== null && (
                <Badge variant="light" color={CONTACT_STATUSES[data.contact_status].color}>
                  {CONTACT_STATUSES[data.contact_status].title}
                </Badge>
              )}
            </Group>
            {data.reject_reason !== null && (
              <Text size="sm" c="dimmed">
                Причина отсева: {data.reject_reason}
              </Text>
            )}
          </Stack>
          <Button
            variant="subtle"
            className="press"
            leftSection={<IconArrowLeft size={16} />}
            onClick={() => void navigate('/donors')}
          >
            К списку
          </Button>
        </Group>
      </Card>

      <SimpleGrid cols={{ base: 2, md: 4 }} spacing="sm">
        <Metric title="DR" value={data.dr ?? '—'} />
        <Metric
          title="Органический трафик"
          value={data.org_traffic === null ? '—' : data.org_traffic.toLocaleString('ru-RU')}
        />
        <Metric
          title="Гео"
          value={data.geo ?? '—'}
          hint={
            data.geo_top_share === null
              ? undefined
              : `доля рынка ${(data.geo_top_share * 100).toFixed(0)}%`
          }
        />
        <Metric
          title="Данные проверены"
          value={when(data.metrics_refreshed_at)}
          hint={
            data.expires_at === null
              ? 'не проверялись'
              : `${data.fresh ? 'в сроке до' : 'срок вышел'} ${when(data.expires_at)}`
          }
        />
      </SimpleGrid>

      {data.geo_breakdown !== null && data.geo_breakdown.length > 0 && (
        <Card className="glass" p="lg">
          <Title order={5} mb="xs">
            Откуда трафик
          </Title>
          <Text size="sm" c="dimmed" mb="sm">
            Проверяется вхождение в топ-5, а не только доля выше порога: страна с долей 12% на
            третьем месте нам подходит.
          </Text>
          <Group gap="xs">
            {data.geo_breakdown.map((row) => (
              <Badge key={row.country} variant="light">
                {row.country} · {(row.share * 100).toFixed(0)}%
              </Badge>
            ))}
          </Group>
        </Card>
      )}

      <Card className="glass" p="xs">
        <Table className="dataTable" verticalSpacing="sm" horizontalSpacing="md">
          <Table.Thead>
            <Table.Tr>
              <Table.Th>Адрес</Table.Th>
              <Table.Th>Откуда</Table.Th>
              <Table.Th>Писали</Table.Th>
              <Table.Th>Отвечали</Table.Th>
            </Table.Tr>
          </Table.Thead>
          <Table.Tbody>
            {data.contacts.map((contact) => (
              <Table.Tr key={contact.id}>
                <Table.Td>{contact.email}</Table.Td>
                <Table.Td>{CONTACT_SOURCES[contact.source]}</Table.Td>
                <Table.Td>{when(contact.last_contacted_at)}</Table.Td>
                <Table.Td>
                  {/* Отвечающий адрес важнее найденного: дальше пишем тому,
                      кто отвечает, а не в ящик, где письмо пролежало неделю. */}
                  {contact.last_replied_at === null ? (
                    '—'
                  ) : (
                    <Badge variant="light" color="green">
                      {when(contact.last_replied_at)}
                    </Badge>
                  )}
                </Table.Td>
              </Table.Tr>
            ))}
          </Table.Tbody>
        </Table>
        {data.contacts.length === 0 && (
          <Text size="sm" c="dimmed" p="lg">
            Адресов нет. Это не тупик: лестница контактов идёт от бесплатных ступеней к платной, и
            домен может закрыться на следующем заходе.
          </Text>
        )}
      </Card>

      {data.last_price !== null && (
        <Card className="glass" p="lg">
          <Title order={5} mb="xs">
            Последняя цена
          </Title>
          {/* Валюта приходит с ценой, а не подставляется здесь: конвертации
              в сервисе нет, и «USD» рядом с числом в евро — это не подпись,
              а неверное число. */}
          <Text>
            {data.last_price} {data.last_price_currency ?? ''} · {when(data.last_price_at)}
          </Text>
        </Card>
      )}
    </Stack>
  );
}
