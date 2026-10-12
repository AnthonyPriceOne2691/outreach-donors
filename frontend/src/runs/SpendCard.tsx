/**
 * Расход прогона по факту — из журнала расхода, всё записанное за его номером
 * (ТЗ: «сколько потрачено юнитов и денег»).
 *
 * **Каждый провайдер — своей единицей**: Ahrefs юнитами, выдача деньгами, модель
 * токенами. В одну сумму они не складываются: 23.09.2026 токены судьи попали в «факт»
 * прогона, и он вышел 26 203 юнита при 901 настоящем. Плитки — по провайдерам, ниже —
 * статьи той же таблицей, что на экране «Расход» (`components/SpendTable`).
 *
 * Смета — у плитки Ahrefs: она покрывает только его юниты. Бесплатные запросы — строкой:
 * не трата, но по ним видно, что повтор действительно ничего не стоит.
 */

import { Card, SimpleGrid, Stack, Text, Title } from '@mantine/core';

import { usageProvider } from '../api/labels';
import type { ArticleCard } from '../api/types';
import { Metric } from '../components/Metric';
import { SpendTable, tileOf } from '../components/SpendTable';
import { formatNumber, plural } from '../format';

/** Провайдеры в порядке прогона: метрики, выдача, модель; прочие — следом. */
const ORDER = ['ahrefs', 'serp', 'llm', 'hunter', 'email'];

function rank(provider: string): number {
  const at = ORDER.indexOf(provider);
  return at < 0 ? ORDER.length : at;
}

interface Total {
  provider: string;
  units: number;
  amount: number;
}

/** Итог по провайдеру — из статей, а не своим запросом: плитка и таблица сходятся. */
function totalsOf(articles: ArticleCard[]): Total[] {
  const totals = new Map<string, Total>();
  for (const article of articles) {
    const was = totals.get(article.provider) ?? { provider: article.provider, units: 0, amount: 0 };
    totals.set(article.provider, {
      provider: article.provider,
      units: was.units + article.units,
      amount: was.amount + Number(article.amount_usd),
    });
  }
  return [...totals.values()];
}

interface Props {
  spending: ArticleCard[];
  /** Смета прогона в юнитах Ahrefs. `null` — до сметы не дошёл. */
  estimated: number | null;
  /** Запросы к Ahrefs, которые обслужил кэш провайдера. */
  free: number;
  /** Прогон ещё в очереди или идёт: пустой журнал — «пока ничего», а не «ничего». */
  active: boolean;
}

export function SpendCard({ spending, estimated, free, active }: Props) {
  // Сортировка устойчивая: внутри провайдера — порядок сервера, по сумме.
  const articles = [...spending].sort((one, two) => rank(one.provider) - rank(two.provider));
  return (
    <Card className="glass" p="md">
      <Stack gap="sm" px="md" pt="xs" pb={articles.length === 0 ? 'xs' : 0}>
        <Title order={5}>Расход по факту</Title>
        {articles.length === 0 ? (
          <Text size="sm" c="dimmed">
            {active
              ? 'Прогон ещё ничего не потратил — расход появится с первым платным запросом.'
              : 'В журнале расхода за этим прогоном записей нет: он ничего не покупал.'}
          </Text>
        ) : (
          <SimpleGrid cols={{ base: 2, sm: 3 }} spacing="sm">
            {totalsOf(articles).map(({ provider, units, amount }) => {
              const tile = tileOf(provider, units, amount);
              const hint =
                provider === 'ahrefs' && estimated !== null
                  ? `${tile.hint ?? ''} · смета ${formatNumber(estimated)}`
                  : tile.hint;
              return (
                <Metric
                  key={provider}
                  title={usageProvider(provider).title}
                  value={tile.value}
                  hint={hint}
                />
              );
            })}
          </SimpleGrid>
        )}
        {free > 0 && (
          <Text size="sm" c="dimmed">
            Бесплатно из кэша Ahrefs: {formatNumber(free)}{' '}
            {plural(free, 'запрос', 'запроса', 'запросов')} — провайдер их не списал.
          </Text>
        )}
      </Stack>
      {articles.length > 0 && <SpendTable articles={articles} />}
    </Card>
  );
}
