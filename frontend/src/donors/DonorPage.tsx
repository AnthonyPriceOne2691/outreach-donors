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
 * же фильтрами: список держит их в адресе и передаёт его сюда. Стоит над
 * заголовком слева — там же, где «К прогонам» у карточки прогона: одно
 * действие на всех экранах в одном месте (`BackLink`).
 *
 * **Номер из адреса проверяется до запроса.** «abc» в адресе раньше уходил
 * на сервер как `NaN`, и экран показывал английский отказ разбора; теперь —
 * «такого донора нет» словами и ссылка к списку.
 *
 * **Одна левая кромка у всех разделов.** Заголовок карточки стоял на 250 px,
 * разделы под ним — на 238, адреса в таблице — на третьей линии (аудит
 * 25.09.2026): поля у разделов теперь те же, что у заголовка, а таблица
 * адресов выступает на поле ячейки, и адрес встаёт на ту же линию.
 */

import { Alert, Badge, Card, Group, Loader, SimpleGrid, Stack, Text, Title } from '@mantine/core';
import { useQuery } from '@tanstack/react-query';
import { useLocation, useParams } from 'react-router-dom';

import { ApiError, refusalOf } from '../api/client';
import { rowIdOf } from '../api/ids';
import { countryTitle, DONOR_STATUSES } from '../api/labels';
import { formatDate, formatNumber, formatShare } from '../format';
import { BackLink, backTo } from '../components/BackLink';
import { Metric } from '../components/Metric';
import { fetchDonor } from '../api/runs';
import { DonorAddresses } from './DonorAddresses';

const when = formatDate;

/** Донора нет: номер негодный или такого нет в базе. */
function NoSuchDonor({ back, said }: { back: string; said: string }) {
  return (
    <Card className="glassPanel" p="xl">
      <Stack gap={6}>
        <BackLink to={back}>К списку</BackLink>
        <Title order={3}>Такого донора нет</Title>
        <Text size="sm" c="dimmed" maw={720}>
          {said} Все доноры — в списке, карточка открывается щелчком по строке.
        </Text>
      </Stack>
    </Card>
  );
}

export function DonorPage() {
  const raw = useParams<{ id: string }>().id;
  const id = rowIdOf(raw);
  const back = `/donors${backTo(useLocation().state)}`;
  const { data, isLoading, error } = useQuery({
    queryKey: ['donor', String(id)],
    queryFn: () => fetchDonor(id ?? 0),
    enabled: id !== null,
  });

  if (id === null) {
    return <NoSuchDonor back={back} said={`«${raw ?? ''}» в адресе — не номер донора.`} />;
  }
  if (error instanceof ApiError && error.status === 404) {
    return <NoSuchDonor back={back} said={`${refusalOf(error)}.`} />;
  }
  if (isLoading) return <Loader aria-label="Загружаем донора" m="md" />;
  if (error) {
    return (
      <Stack gap="lg">
        <BackLink to={back}>К списку</BackLink>
        <Alert color="red" title="Донор не загрузился">
          {refusalOf(error)}
        </Alert>
      </Stack>
    );
  }
  if (data === undefined) return null;

  return (
    <Stack gap="lg">
      <Card className="glassPanel" p="xl">
        <Stack gap={6}>
          <BackLink to={back}>К списку</BackLink>
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
        <Card className="glass" p="xl">
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

      {/* Ключ по донору: номер задачи поиска помнится по донору, и карточка
          другого донора не должна унаследовать его от предыдущей. */}
      <DonorAddresses key={data.id} donor={data} />

      {data.last_price !== null && (
        <Card className="glass" p="xl">
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
