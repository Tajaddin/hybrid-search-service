import { expect, test } from '@playwright/test';

test('search with correction and facets, interleaving with a click, evaluation table', async ({ page }) => {
  await page.goto('/');
  await expect(page.getByRole('heading', { name: 'Hybrid product search' })).toBeVisible();
  await expect(page.getByTestId('health')).toContainText('products');

  // The default query is misspelled on purpose: the correction must show and results must come back.
  await expect(page.getByTestId('hit').first()).toBeVisible({ timeout: 60_000 });
  await expect(page.getByTestId('understood')).toContainText('corrected from');

  await page.getByTestId('q').fill('nike running shoes');
  await page.getByTestId('ranker').selectOption('hybrid_rerank');
  await page.getByTestId('go').click();
  await expect(page.getByTestId('understood')).toContainText('rerank', { timeout: 60_000 });
  const hits = page.getByTestId('hit');
  await expect(hits.first()).toBeVisible();
  const facet = page.getByTestId('facet-brand').first();
  if (await facet.isVisible()) {
    await facet.click();
    await expect(page.getByTestId('facet-brand').first()).toHaveClass(/active/);
  }

  await page.getByTestId('tab-interleave').click();
  await page.getByTestId('iq').fill('bluetooth speaker');
  await page.getByTestId('igo').click();
  await expect(page.getByTestId('ihit').first()).toBeVisible({ timeout: 60_000 });
  await page.getByTestId('iclick').first().click();
  await expect(page.getByTestId('clicks')).toContainText('1 click');

  await page.getByTestId('tab-eval').click();
  await expect(page.getByTestId('eval-table')).toContainText('hybrid', { timeout: 15_000 });
});
