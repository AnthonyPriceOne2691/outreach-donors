/**
 * Обзор: что молчит, где мы и что ждёт человека.
 *
 * Главная отвечает числами, а не описанием разделов (замечание 25.09.2026):
 * карта сервиса из шести плиток-пояснений читалась один раз, а открывают
 * главную каждый день — и каждый раз за тем, чтобы узнать, что изменилось
 * и что делать. Поэтому сверху работа для человека, ниже — доноры, письма
 * и расход. Каждое число посчитано сервером тем же правилом, что на своём
 * экране: своё правило на фронте разошлось бы с экраном при первой правке.
 */

import { Alert, Anchor, Badge, Card, Group, Loader, SimpleGrid, Stack, Text } from '@mantine/core';
import { useQuery } from '@tanstack/react-query';
import { Link } from 'react-router-dom';

import { refusalOf } from '../api/client';
import { countryTitle, RUN_STATUSES } from '../api/labels';
import { fetchOverview } from '../api/overview';
import type {
  LetterTransport,
  OverviewDonors,
  OverviewLetters,
  OverviewView,
  RunCard,
} from '../api/types';
import { useSession } from '../auth/AuthProvider';
import { Meter } from '../components/Meter';
import { PageHead } from '../components/PageHead';
import { Metric } from '../components/Metric';
import { formatDateTime, formatDay, formatNumber, formatShare, formatUsd, plural } from '../format';
import { mailLine } from '../letters/mailTile';
import { attention, Section, WaitingSection } from './OverviewWaiting';
import { Watchdog } from './Watchdog';

function priceHint(donors: OverviewDonors): string {
  if (donors.priced === 0) return 'цен ещё нет';
  if (donors.priced_fresh === donors.priced) return 'все цены свежие';
  return `свежих ${formatNumber(donors.priced_fresh)}`;
}

/**
 * Под «Ответили»: доля написанных — или почему её нет.
 *
 * «Написали» считает принятых доноров (как вся воронка), а «Ушло» в письмах
 * донорам — все письма этапа, и доменам, которых человек не принял или
 * отклонил потом. Ноль в воронке рядом с «Ушло 24» поэтому не значит, что
 * писем не было: до 10.10.2026 здесь стояло «писем ещё не было» — неправда
 * на том же экране (проверка QA 10.10.2026). Так говорится, только когда
 * донорам не ушло ни одного письма.
 */
function repliedHint(donors: OverviewDonors, sent: number): string {
  if (donors.written > 0) return `${formatShare(donors.replied / donors.written)} написанных`;
  return sent > 0 ? 'принятым донорам не писали' : 'писем ещё не было';
}

/**
 * Воронка: от проверенного домена до донора с ценой (решение 26.09.2026).
 *
 * «Донор» здесь значит одно — домен, принятый человеком: то же, что на экране
 * «Доноры», куда ведёт плитка. Записей в базе больше — у каждого домена, за
 * чьи метрики заплатил прогон, — и это «проверено доменов», а не доноры:
 * «доноров 1 065» над пустым списком доноров читалось бы как сбой. Всё, что
 * ниже «Доноров», считается среди них — тем же правилом, что у списка.
 */
function DonorsSection({ donors, sent }: { donors: OverviewDonors; sent: number }) {
  const checked = donors.total - donors.unchecked;
  return (
    // Ссылки у раздела нет: «Доноры» — плитка-ссылка внутри, и две ссылки
    // на один экран в одном разделе читались бы как два разных места.
    <Section title="Воронка доноров">
      <SimpleGrid cols={{ base: 2, sm: 4, lg: 7 }} spacing="sm" className="metricGrid">
        <Metric
          title="Проверено доменов"
          value={formatNumber(donors.total)}
          hint={donors.unchecked > 0 ? `не проверено ${formatNumber(donors.unchecked)}` : undefined}
        />
        <Metric
          title="Прошли пороги"
          value={formatNumber(donors.suitable)}
          hint={checked > 0 ? `${formatShare(donors.suitable / checked)} проверенных` : undefined}
        />
        {/* «Доноры» — принятые человеком. Не «Приняты»: на «Отборе» «Приняты» —
            вкладка судьи и порогов, и одно слово с двумя числами на двух
            экранах читалось бы как расхождение. */}
        <Metric
          title="Доноры"
          value={formatNumber(donors.accepted)}
          hint={
            donors.rejected > 0
              ? `отклонено ${formatNumber(donors.rejected)}`
              : donors.accepted > 0
                ? 'отклонённых нет'
                : 'решений не было'
          }
          to="/donors"
        />
        {/* «С адресом» — адрес есть в базе: правило фильтра списка доноров и ступени писем
            (проверка прода 10.10.2026: здесь 22, там 23 при «Написали 23»). Письмо уходит
            только на адрес — «Написали» не больше; кончились ли адреса, скажут «Письма». */}
        <Metric
          title="С адресом"
          value={formatNumber(donors.with_email)}
          hint={donors.form_only > 0 ? `и ${formatNumber(donors.form_only)} с формой` : undefined}
          to="/donors?has_contact=true"
        />
        {/* Кому из принятых ушло хоть одно письмо — это и сказано под числом:
            «Ушло» ниже считает письма всем доменам, и ноль здесь рядом с ним
            читался сбоем (проверка QA 10.10.2026). */}
        <Metric title="Написали" value={formatNumber(donors.written)} hint="принятым донорам" />
        <Metric
          title="Ответили"
          value={formatNumber(donors.replied)}
          hint={repliedHint(donors, sent)}
        />
        <Metric title="С ценой" value={formatNumber(donors.priced)} hint={priceHint(donors)} />
      </SimpleGrid>
    </Section>
  );
}

/**
 * Ответы, не привязанные ни к одному нашему письму, — строкой под плитками
 * писем, и только когда они есть. Не плиткой в «Ждут человека»: убрать такой
 * ответ оттуда пока нечем (привязки руками нет), и янтарное число стояло бы
 * вечно — с первого же ответа на пробное письмо.
 */
function UnboundLine({ count }: { count: number }) {
  if (count === 0) return null;
  return (
    <Text size="sm">
      Не привязаны ни к одному нашему письму:{' '}
      {/* Чернилами и жирным, как числа в соседних строках сводки, — ссылку
          выдаёт подчёркивание. Бирюзовая на светлом месте стекла намерилась
          на 4,04 : 1 средним весом и 4,42 жирным при норме 4,5 (28.09.2026). */}
      <Anchor
        component={Link}
        to="/threads?tab=unbound"
        size="sm"
        fw={700}
        c="var(--ink)"
        underline="always"
        className="inkLink"
      >
        {formatNumber(count)} {plural(count, 'ответ', 'ответа', 'ответов')}
      </Anchor>
    </Text>
  );
}

/** Письма Этапа 2 строкой. Без права «Рекламодатели» этапа в сводке нет вовсе — и строки нет. */
function StageTwoLine({ letters }: { letters: OverviewLetters | undefined }) {
  if (letters === undefined) return null;
  return (
    <Text size="sm" c="dimmed">
      {letters.queued === 0 && letters.sent === 0
        ? 'Рекламодателям писем ещё не было.'
        : `Рекламодателям: в очереди ${formatNumber(letters.queued)}, ушло ${formatNumber(letters.sent)}, дошло ${formatNumber(letters.delivered)}.`}
    </Text>
  );
}

function LettersSection({
  title,
  to,
  letters,
  advertisers,
  transport,
  unbound,
}: {
  title: string;
  to: string;
  letters: OverviewLetters;
  /** Строка Этапа 2 под письмами донорам; у роли «Рекламодатели» он сам в плитках. */
  advertisers?: OverviewLetters | undefined;
  transport: LetterTransport;
  unbound: number;
}) {
  const mail = mailLine(transport);
  return (
    <Section
      title={title}
      to={to}
      toTitle="К письмам"
      aside={
        // Слово то же, что в плитке «Почта» на экране писем (`mailTile`):
        // «не подключена», а что с письмами — фразой под плитками.
        <Badge variant="light" color={transport.real ? 'green' : 'yellow'}>
          {transport.real ? 'почта подключена' : 'почта не подключена'}
        </Badge>
      }
    >
      <Stack gap="sm">
        <SimpleGrid cols={{ base: 2, sm: 4 }} spacing="sm">
          <Metric title="В очереди" value={formatNumber(letters.queued)} />
          <Metric title="Ушло" value={formatNumber(letters.sent)} />
          <Metric title="Дошло" value={formatNumber(letters.delivered)} />
          {/* Слово и цвет — как у состояния диалога: отказ доставки требует
              внимания (адрес выбыл), но это не отказ донора. */}
          <Metric
            title="Отказ доставки"
            value={formatNumber(letters.bounced)}
            color={attention(letters.bounced)}
          />
        </SimpleGrid>
        <UnboundLine count={unbound} />
        {mail !== null && (
          <Text size="sm" c="dimmed">
            {mail}
          </Text>
        )}
        <StageTwoLine letters={advertisers} />
      </Stack>
    </Section>
  );
}

/** Последний прогон: что запускали и чем кончился. Очередь рассмотрения
 *  здесь не повторяется — она в «Ждут человека», и два «Рассмотреть» на одном
 *  экране читаются как сбой, а не как забота. */
function LastRunLine({ run }: { run: RunCard | null }) {
  if (run === null) {
    return <Text size="sm">Прогонов ещё не было.</Text>;
  }
  const status = RUN_STATUSES[run.status];
  return (
    <Stack gap={4}>
      <Group gap="xs" align="center">
        <Text fw={600}>Прогон №{run.id}</Text>
        <Text>{countryTitle(run.country)}</Text>
        <Badge variant="light" color={status.color}>
          {status.title}
        </Badge>
      </Group>
      <Text size="sm" c="dimmed">
        {formatDateTime(run.started_at)} · ключей {formatNumber(run.keywords)} · доменов{' '}
        {formatNumber(run.hosts)} ·{' '}
        {run.actual_units !== null
          ? `потрачено ${formatNumber(run.actual_units)} юн.`
          : `смета ${formatNumber(run.estimated_units)} юн.`}
      </Text>
    </Stack>
  );
}

function RunSection({ data }: { data: OverviewView }) {
  const { can } = useSession();
  return (
    <Section title="Прогон и расход" to="/run" toTitle="К прогонам">
      <Stack gap="md">
        <LastRunLine run={data.last_run} />
        <Stack gap={6} className="hairline" pt="md">
          {/* «Потрачено из потолка» — одна единица смысла: на телефоне строка
              рвалась между «из» и потолком (аудит 25.09.2026, №28). Период — цикл ключа
              (16.10.2026): кап считает его, а не месяц, и выдача ниже — с первого числа. */}
          <Text size="sm">
            Юниты Ahrefs {data.ahrefs_by_key ? 'за цикл ключа, ' : ''}с{' '}
            {formatDay(data.ahrefs_since)}:{' '}
            <span style={{ whiteSpace: 'nowrap' }}>
              <b>{formatNumber(data.ahrefs_units)}</b>
              {data.ahrefs_cap === null
                ? ' — кап неизвестен'
                : ` из ${formatNumber(data.ahrefs_cap)}`}
            </span>
          </Text>
          {data.ahrefs_cap !== null && (
            <Meter
              spent={data.ahrefs_units}
              cap={data.ahrefs_cap}
              label="Юниты Ahrefs за цикл ключа"
            />
          )}
          {can('usage') ? (
            <Text size="sm" c="dimmed">
              По нашему капу — он от ключа Ahrefs. Ключ, его цикл и остаток у провайдера — на экране{' '}
              <Anchor component={Link} to="/usage" size="sm">
                расхода
              </Anchor>
              .
            </Text>
          ) : null}
        </Stack>
        <Text size="sm">
          Выдача с начала месяца: <b>{formatUsd(data.serp_usd)}</b>
        </Text>
      </Stack>
    </Section>
  );
}

function Dashboard() {
  const { data, isLoading, error } = useQuery({
    queryKey: ['overview'],
    queryFn: fetchOverview,
    // Числа двигает работа людей и фоновых задач — минуты, а не секунды.
    refetchInterval: 60 * 1000,
  });

  if (isLoading) {
    return (
      <Card className="glass" p="lg">
        <Group gap="xs">
          <Loader aria-label="Считаем сводку" size="xs" />
          <Text size="sm" c="dimmed">
            Считаем сводку по базе.
          </Text>
        </Group>
      </Card>
    );
  }
  if (error) {
    return (
      <Alert color="red" title="Сводка не загрузилась">
        {refusalOf(error)}
      </Alert>
    );
  }
  if (data === undefined) return null;

  // Этапа без права в ответе нет вовсе: без него нет воронки, писем донорам и прогонов.
  const { donors, advertisers } = data.letters;
  return (
    <>
      <WaitingSection waiting={data.waiting} />
      {donors !== undefined ? (
        <>
          <DonorsSection donors={data.donors} sent={donors.sent} />
          <SimpleGrid cols={{ base: 1, md: 2 }} spacing="lg">
            <LettersSection
              title="Письма донорам"
              to="/letters?stage=donors"
              letters={donors}
              advertisers={advertisers}
              transport={data.transport}
              unbound={data.unbound_replies}
            />
            <RunSection data={data} />
          </SimpleGrid>
        </>
      ) : advertisers !== undefined ? (
        <LettersSection
          title="Письма рекламодателям"
          to="/letters?stage=advertisers"
          letters={advertisers}
          transport={data.transport}
          unbound={data.unbound_replies}
        />
      ) : null}
    </>
  );
}

/** Подсказка «i» у заголовка — как читать экран. */
const OVERVIEW_HINT =
  'Сверху — работа, которая ждёт человека: те же числа стоят у пунктов меню. Ниже — воронка доноров, письма и расход. Каждое число посчитано тем же правилом, что на экране раздела.';

/** То же без Этапа 1: воронки и расхода на экране нет. */
const OVERVIEW_HINT_NO_DONORS =
  'Сверху — работа, которая ждёт человека: те же числа стоят у пунктов меню. Ниже — письма. Каждое число посчитано тем же правилом, что на экране раздела.';

export function OverviewPage() {
  const { can } = useSession();

  return (
    <Stack gap="lg">
      {/* Шапка — одним заголовком. Кто вошёл, его права и «Сменить пароль» — в меню
          у почты в шапке рамы, на любом экране (`AccountMenu`); до 09.10.2026 они
          стояли здесь строкой над «Ждут человека» (аудит экранов 09.10.2026). */}
      <Card className="glassPanel" px="xl" py="lg">
        <PageHead title="Обзор" hint={can('donors') ? OVERVIEW_HINT : OVERVIEW_HINT_NO_DONORS} />
      </Card>

      <Watchdog />

      {can('view') && <Dashboard />}
    </Stack>
  );
}
