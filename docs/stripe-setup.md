# Stripe setup for SmartBetSports Founding Membership

SmartBetSports launches with one plan: **SmartBetSports Founding Membership** at **$4.99/month**. Existing subscribers stay attached to the Stripe Price used when they subscribed; future pricing should use a new Price ID rather than silently migrating existing subscriptions.

## Required environment variables

Set backend variables outside source control:

- `STRIPE_SECRET_KEY`
- `STRIPE_WEBHOOK_SECRET`
- `STRIPE_FOUNDING_MONTHLY_PRICE_ID`
- `STRIPE_SUCCESS_URL=http://localhost:3000/billing/success`
- `STRIPE_CANCEL_URL=http://localhost:3000/subscribe`
- `FRONTEND_ORIGIN=http://localhost:3000`

Optional frontend variable: `NEXT_PUBLIC_STRIPE_PUBLISHABLE_KEY`.

## Test mode setup

1. In Stripe test mode, create a product named `SmartBetSports Founding Membership`.
2. Create a recurring monthly Price for USD 4.99 and copy its Price ID to `STRIPE_FOUNDING_MONTHLY_PRICE_ID`.
3. Forward webhooks locally with `stripe listen --forward-to localhost:8000/api/billing/webhook`.
4. Copy the webhook signing secret to `STRIPE_WEBHOOK_SECRET`.
5. Start Checkout from `/subscribe`, complete payment with Stripe test cards, and confirm the webhook activates access.
6. Test cancellation and payment-method updates through the Stripe Customer Portal. Enable the Customer Portal in the Stripe Dashboard before using it.
7. Test payment failure with Stripe test payment methods and confirm the app shows inactive/past-due status.
8. Switch to live keys and a live Price ID only after test mode works end-to-end.

## Creating an owner/test promotion code

Do not store private promotion codes in GitHub, tests, screenshots, logs, or environment examples.

1. Create a 100%-off coupon in Stripe.
2. Choose whether it lasts forever or for a limited duration.
3. Create a promotion code for that coupon.
4. Set max redemptions to 1 where appropriate.
5. Optionally restrict the promotion code to a specific Stripe customer.
6. Enter it through Stripe Checkout by clicking **Add promotion code**.
7. Confirm the webhook activates access from the resulting Stripe subscription.
8. Deactivate or rotate the code when finished.

Promotion access is not a fake local subscription. Stripe must accept the code and create a subscription; webhooks then update the local entitlement.
