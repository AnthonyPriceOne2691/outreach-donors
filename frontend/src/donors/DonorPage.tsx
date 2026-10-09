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
 *
 * **Донор — только принятый человеком** (решение 26.09.2026). Карточка
 * открывается у любой записи базы, но шапка говорит правду: запись без
 * решения — кандидат, ждёт решения в очереди прогона; отклонённая — не донор.
 * Решают не здесь, а в очереди прогона, и шапка ведёт туда.
 */

import {
  Alert,
  Anchor,
  Badge,
  Card,
  Group,
  Loader,
  Grid,
  SimpleGrid,
  Stack,
  Text,
  Title,
} from '@mantine/core';
import { useQuery } from '@tanstack/react-query';
import { Link, useLocation, useParams } from 'react-router-dom';

import { ApiError, refusalOf } from '../api/client';
import { rowIdOf } from '../api/ids';
import { countryTitle, DONOR_STATUSES } from '../api/labels';
import type { DonorFullCard } from '../api/types';
import { formatDate, formatNumber, formatShare } from '../format';
import { BackLink, backTo } from '../components/BackLink';
import { InfoHint } from '../components/InfoHint';
import { Metric } from '../components/Metric';
import { Seams } from '../components/Seams';
import { fetchDonor } from '../api/runs';
import { DonorAddresses } from './DonorAddresses';
import { DonorPrice } from './DonorPrice';

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

/** Кто и когда решил: «принят claude@site.com 06.10.2026». */
function decidedBy(donor: DonorFullCard): string {
  const who = donor.review_by ? ` ${donor.review_by}` : '';
  const day = donor.review_at ? ` ${when(donor.review_at)}` : '';
  return `${who}${day}`;
}

/** Донор: кем принят и в каком прогоне. Донор, заведённый вручную с ценой
 *  (07.10.2026), в очереди прогона не стоял — так и сказано: «заведён вручную». */
function AcceptedStanding({ donor }: { donor: DonorFullCard }) {
  const run = donor.review_run;
  const how = donor.entered_by && run === null ? 'заведён вручную' : 'принят';
  return (
    <Text size="sm" className="donorStanding">
      Донор: {how}
      {decidedBy(donor)}
      {run === null ? '' : ' '}
      {run === null ? null : (
        <Anchor component={Link} to={`/runs/${run}/review`} size="sm" fw={500}>
          в очереди прогона №{run}
        </Anchor>
      )}
      .
    </Text>
  );
}

/** Решение человека по домену: донор — кем принят и в каком прогоне;
 *  не донор — кто тогда и где о нём решают. До 06.10.2026 у донора не
 *  было ничего: «принят» было видно в списке, а кем и когда — нигде. */
function Standing({ donor }: { donor: DonorFullCard }) {
  if (donor.review === 'accepted') {
    return <AcceptedStanding donor={donor} />;
  }
  const run = donor.review_run;
  const where =
    run === null ? null : (
      <Anchor component={Link} to={`/runs/${run}/review`} size="sm" fw={500}>
        в очереди прогона №{run}
      </Anchor>
    );
  if (donor.review === 'rejected') {
    return (
      <Text size="sm" className="donorStanding">
        Не донор: отклонён{decidedBy(donor) || ' человеком'}
        {where === null ? '' : ' '}
        {where}. Решение можно снять там же.
      </Text>
    );
  }
  return (
    <Text size="sm" className="donorStanding">
      {where === null ? (
        // Не решали и в очереди его нет: либо не прошёл пороги, либо запись
        // старше очередей рассмотрения.
        'Не донор: проверенный домен, решения человека по нему нет, в очередях прогонов он не стоит.'
      ) : (
        <>Кандидат, а не донор: ждёт решения человека {where}.</>
      )}
    </Text>
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
            {/* Домен шире карточки на телефоне срезался её краем; теперь
                переносится по точкам и дефисам (06.10.2026). */}
            <Title order={3} className="cellName">
              <Seams text={data.host} />
            </Title>
            {/* Исход поиска адреса — в разделе «Адреса», а не здесь:
                один и тот же значок дважды читается как сбой. */}
            <Badge variant="light" color={DONOR_STATUSES[data.status].color}>
              {DONOR_STATUSES[data.status].title}
            </Badge>
          </Group>
          <Standing donor={data} />
          {data.reject_reason !== null && (
            <Text size="sm" c="dimmed">
              Причина отсева: {data.reject_reason}
            </Text>
          )}
          {/* Плитки — в шапке, а не отдельным рядом на полотне: в тёмной теме они
              читались провалами, а пять карточек во всю ширину по 1–2 строки делали из
              карточки донора простыню (аудит экранов 09.10.2026). */}
          <SimpleGrid cols={{ base: 2, md: 4 }} spacing="sm" mt="sm">
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
          {/* Разбивка по странам — строкой значков под плитками, и только когда стран
              больше одной: при неполной разбивке была одна страна — та же, что в плитке
              «Гео», — и абзац-оправдание к ней. Пояснение — в «i». */}
          {data.geo_breakdown !== null && data.geo_breakdown.length > 1 && (
            <Group gap="xs" mt="xs">
              <Text size="sm">Откуда трафик</Text>
              {data.geo_breakdown.map((row) => (
                <Badge key={row.country} variant="light">
                  {countryTitle(row.country)} · {formatShare(row.share)}
                </Badge>
              ))}
              <InfoHint name="Как проверяется страна" width={320}>
                {data.geo_partial
                  ? 'Спрошена только верхняя страна: она же целевая, а значит донор в топ-5 при любом раскладе — остальные строки вердикт не меняют и стоили бы впятеро дороже.'
                  : 'Проверяется вхождение в топ-5, а не только доля выше порога: страна с долей 12% на третьем месте нам подходит.'}
              </InfoHint>
            </Group>
          )}
        </Stack>
      </Card>

      {/* Ниже — две колонки на широком окне: адреса шире, цена рядом (аудит
          09.10.2026: пять карточек во всю ширину по 1–2 строки содержимого). */}
      <Grid gutter="lg" align="flex-start">
        <Grid.Col span={{ base: 12, lg: 7 }}>
          {/* Ключ по донору: номер задачи поиска помнится по донору, и карточка
              другого донора не должна унаследовать его от предыдущей. */}
          <DonorAddresses key={data.id} donor={data} />
        </Grid.Col>
        <Grid.Col span={{ base: 12, lg: 5 }}>
          {/* Цена, откуда она и «Указать цену» — тоже по ключу донора: вписанное
              в поля одного донора не должно уехать в карточку другого. */}
          <DonorPrice key={`price-${data.id}`} donor={data} />
        </Grid.Col>
      </Grid>
    </Stack>
  );
}
