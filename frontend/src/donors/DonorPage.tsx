/**
 * Карточка донора: метрики, срок годности, адреса и ступень, которая
 * их дала, — и поиск адреса, если его нет (`DonorAddresses`).
 *
 * **Срок годности показан датой, а не словом «свежие».** За данные
 * в сроке уже заплачено, и повторный прогон их не трогает — человек,
 * который видит дату, понимает, когда домен снова будет стоить юнитов.
 *
 * **Ступень рядом с адресом** нужна по той же причине: адрес со страницы
 * сайта и адрес из платного сервиса стоили разного, и решение «добирать
 * ли платным» принимают, глядя на это.
 *
 * **«К списку» возвращает туда, откуда пришли** — на ту же страницу с теми
 * же фильтрами: список держит их в адресе и передаёт его сюда.
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
  Text,
  Title,
} from '@mantine/core';
import { IconArrowLeft } from '@tabler/icons-react';
import { useQuery } from '@tanstack/react-query';
import { useLocation, useNavigate, useParams } from 'react-router-dom';

import { refusalOf } from '../api/client';
import { countryTitle, DONOR_STATUSES } from '../api/labels';
import { formatDate, formatNumber, formatShare } from '../format';
import { Metric } from '../components/Metric';
import { fetchDonor } from '../api/runs';
import { DonorAddresses } from './DonorAddresses';

const when = formatDate;

/** Адрес списка, из которого пришли: строка параметров с фильтрами и
 *  страницей. Пришли не из списка — просто список. */
function backTo(state: unknown): string {
  if (state && typeof state === 'object' && 'from' in state && typeof state.from === 'string') {
    return state.from.startsWith('?') ? state.from : '';
  }
  return '';
}

export function DonorPage() {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const location = useLocation();
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
    <Stack gap="lg">
      <Card className="glassPanel" p="xl">
        <Group justify="space-between" align="flex-start">
          <Stack gap={6}>
            <Group gap="sm">
              <Title order={3}>{data.host}</Title>
              {/* Исход поиска адреса — в разделе «Адреса», а не здесь:
                  один и тот же значок дважды читается как сбой. */}
              <Badge variant="light" color={DONOR_STATUSES[data.status].color}>
                {DONOR_STATUSES[data.status].title}
              </Badge>
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
            onClick={() => void navigate(`/donors${backTo(location.state)}`)}
          >
            К списку
          </Button>
        </Group>
      </Card>

      <SimpleGrid cols={{ base: 2, md: 4 }} spacing="sm">
        <Metric title="DR" value={data.dr ?? '—'} />
        <Metric title="Органический трафик" value={formatNumber(data.org_traffic)} />
        <Metric
          title="Гео"
          value={data.geo === null ? '—' : countryTitle(data.geo)}
          hint={
            data.geo_top_share === null
              ? undefined
              : `доля рынка ${formatShare(data.geo_top_share)}`
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
            {data.geo_partial
              ? 'Спрошена только верхняя страна: она же целевая, а значит донор в топ-5 при любом раскладе — остальные строки вердикт не меняют и стоили бы впятеро дороже.'
              : 'Проверяется вхождение в топ-5, а не только доля выше порога: страна с долей 12% на третьем месте нам подходит.'}
          </Text>
          <Group gap="xs">
            {data.geo_breakdown.map((row) => (
              <Badge key={row.country} variant="light">
                {countryTitle(row.country)} · {formatShare(row.share)}
              </Badge>
            ))}
          </Group>
        </Card>
      )}

      <DonorAddresses donor={data} />

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
