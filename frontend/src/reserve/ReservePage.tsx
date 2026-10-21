/**
 * Резерв: домены прогонов, которые не прошли, — с причиной, метриками и местом страны,
 * и «Взять на рассмотрение» (вопрос оператора 20.10.2026: «куда деваются остальные,
 * можно ли их вынести в отдельную базу, чтобы при нехватке выбирать из них»).
 *
 * **Где живёт — вкладкой «Доноров»** (`DonorsTabs`): не прошедшие и раньше лежали там же,
 * под «без решения» и «не подходит». Резерв одного прогона — тот же экран с `?run=N`:
 * в него ведут отчёт прогона («Отсеяно по порогам») и его очередь.
 *
 * **Взятый — в очереди того прогона, где он в резерве**: там его ключи, страна и пороги,
 * там о нём и решают — как о любом кандидате, только строка говорит, почему прогон его не
 * предложил. Решение по домену одно: принятый, отклонённый и ждущий в другой очереди не
 * берутся — сказано почему.
 *
 * **«Кому не пишем» — видно, но не берётся** (ТЗ, решение владельца 11.10.2026): крупный
 * сайт, платформа, соцсеть, СМИ, справочник, гос. зона — причина вместо кнопки.
 *
 * Страница — на сервере и в адресе; домен без фильтра по прогону — одной строкой
 * последнего прогона, где он в резерве.
 */

import { Anchor, Card, Group, Loader, Stack, Text } from '@mantine/core';
import { useState } from 'react';
import { Link } from 'react-router-dom';

import { runInput } from '../api/competitors';
import { countryTitle } from '../api/labels';
import { useSession } from '../auth/AuthProvider';
import { PageHead } from '../components/PageHead';
import { PageSwitch } from '../components/PageSwitch';
import { useUrlFilters } from '../components/useUrlFilters';
import { DonorsTabs } from '../donors/DonorsTabs';
import { formatNumber, plural } from '../format';
import type { ReserveCard, ReserveView } from './reserveApi';
import { isFiltered, NO_FILTERS, readReserve, writeReserve } from './reserveFilters';
import type { ReserveFilters } from './reserveFilters';
import { ReserveTable } from './ReserveTable';
import { useReserve, useTake } from './useReserve';

const HINT =
  'Домены прогонов, которые не прошли: пороги прогона (слабые метрики), регион — страна прогона не в топ-5 и даёт меньше доли, «не проверен», когда замеру не хватило данных, — и «кому не пишем» (крупный сайт, платформа, соцсеть, крупное СМИ, справочник, гос. зона). Вердикт — того прогона, его страной и порогами. «Взять на рассмотрение» кладёт домен в очередь того прогона, где о нём и решают; «кому не пишем» не берётся. Стоп-лист, поставщики, молчание и отклонённые человеком — не резерв: они названы в отчёте прогона, а появились после прогона — домен не берётся.';

const domainsWord = (count: number) => plural(count, 'домен', 'домена', 'доменов');

/** Резерв одного прогона — строкой под заголовком: какой прогон и вход в его очередь. */
function RunLine({ view }: { view: ReserveView }) {
  const run = view.run;
  if (run === null) return null;
  return (
    <Group gap="sm">
      <Text size="sm">
        Прогон №{run.id} · {countryTitle(run.country)}, {runInput(run)} — его не прошедшие.
      </Text>
      <Anchor
        component={Link}
        to={`/runs/${run.id}/review`}
        size="sm"
        c="var(--ink)"
        underline="always"
      >
        Рассмотрение прогона
      </Anchor>
      <Anchor component={Link} to="/donors/reserve" size="sm" c="var(--ink)" underline="always">
        Весь резерв
      </Anchor>
    </Group>
  );
}

/** Заголовок: вкладки раздела, «Резерв» со счётом и строка прогона, если резерв — его. */
function Head({ data, filters }: { data: ReserveView | undefined; filters: ReserveFilters }) {
  return (
    <>
      <DonorsTabs tab="reserve" />
      <PageHead title="Резерв" hint={HINT}>
        {data !== undefined && (
          <Text size="sm" c="var(--ink)">
            {isFiltered(filters) ? 'найдено' : 'всего'} {formatNumber(data.total)}{' '}
            {domainsWord(data.total)}
          </Text>
        )}
      </PageHead>
      {data !== undefined && <RunLine view={data} />}
    </>
  );
}

export function ReservePage() {
  const { can } = useSession();
  const { filters, apply, turn, reset } = useUrlFilters(readReserve, writeReserve);
  const [taking, setTaking] = useState<ReserveCard | null>(null);
  const reserve = useReserve(filters, turn);
  const take = useTake();

  if (reserve.loading) return <Loader aria-label="Загружаем резерв" m="md" />;
  return (
    <Card className="glassPanel" p="lg">
      <Stack gap="sm">
        <Head data={reserve.data} filters={filters} />
        <ReserveTable
          view={reserve.data}
          filters={filters}
          stale={reserve.stale}
          refusal={reserve.refusal}
          empty={reserve.empty}
          mayTake={can('prices')}
          busy={take.isPending}
          taking={take.isPending ? taking : null}
          onFilter={apply}
          onReset={() => reset({ ...NO_FILTERS, run: filters.run })}
          onTake={(row) => {
            setTaking(row);
            take.mutate(row);
          }}
        />
        <PageSwitch
          label="Страницы резерва"
          page={filters.page}
          pages={reserve.pages}
          onChange={turn}
        />
      </Stack>
    </Card>
  );
}
