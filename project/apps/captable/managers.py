from django.db import models
from django.db.models import Sum, Max
from django.db.models import get_model
from django.db.models.query import QuerySet

from .constants import *



class SecurityQuerySet(QuerySet):
    def authorized(self):
        return self.aggregate(t=Sum('addition__authorized'))['t']


class CertificateQuerySet(QuerySet):
    @property
    def liquidated(self):
        """Calculates the as-converted share totals"""
        certificates = self.select_related()
        return sum(filter(
            None, [t.liquidated for t in certificates]))

    @property
    def preference(self):
        certificates = self.select_related()
        return sum(filter(
            None, [t.preference for t in certificates]))

    @property
    def paid(self):
        certificates = self.select_related()
        return sum(filter(
            None, [t.paid for t in certificates]))

    @property
    def outstanding_debt(self):
        certificates = self.select_related()
        return sum(filter(
            None, [t.outstanding_debt for t in certificates]))

    @property
    def converted(self):
        certificates = self.select_related()
        return sum(filter(
            None, [t.converted for t in certificates]))

    def discounted(self, pre_valuation=None):
        certificates = self.select_related()
        return sum(filter(
            None, [t.discounted(pre_valuation) for t in certificates]))

    def prorata(self, new_shares):
        certificates = self.select_related()
        return sum(filter(
            None, [t.prorata(new_shares) for t in certificates]))

    def exchanged(self, pre_valuation, price):
        certificates = self.select_related()
        return sum(filter(
            None, [t.exchanged(pre_valuation, price) for t in certificates]))

    @property
    def granted(self):
        """Calculates the total number of granted options.

        We need to differentiate between the concept of granted and
        extended because options that have been granted and exercised
        (either due to early exercise or normal exercise) are still counted
        against the total available option pool.  Meaning: the number
        of available options does not increase simply because those options
        have turned into shares.  The total number of options in the pool has
        a fixed limit to avoid inadvertent dilution"""

        certificates = self.select_related().filter(
            security__security_type=SECURITY_TYPE_OPTION).aggregate(
                t=Sum('granted'))['t']
        return certificates

    @property
    def exercised(self):
        """Calculates the total amount of exercised options"""
        certificates = self.select_related().filter(
            security__security_type=SECURITY_TYPE_OPTION).aggregate(
                t=Sum('exercised'))['t']
        return certificates

    @property
    def cancelled(self):
        """Calculates the granted but not exercised (ie, outstanding) options"""
        certificates = self.select_related().filter(
            security__security_type=SECURITY_TYPE_OPTION).aggregate(
                t=Sum('cancelled'))['t']
        return certificates

    @property
    def outstanding_options(self):
        """Calculates the granted but not exercised (ie, outstanding) options"""
        # TODO Normally this would be called 'outstanding', but that is
        # an overloaded term.
        if self.granted:
            return self.granted - self.exercised - self.cancelled
        else:
            return 0

    @property
    def warrants(self):
        """Calculates total warrants issued"""
        certificates = self.select_related()
        warrants = sum(filter(
            None, [c.outstanding_warrants for c in certificates]))
        if warrants:
            return warrants
        else:
            return 0

    @property
    def available(self):
        pool = self.select_related().filter(
            security__security_type=SECURITY_TYPE_OPTION).aggregate(
                t=Sum('security__addition__authorized'))['t']
        return pool - self.granted + self.cancelled


    @property
    def vested(self):
        certificates = self.select_related()
        return sum(filter(
            None, [t.vested for t in certificates]))

    @property
    def outstanding_shares(self):
        certificates = self.select_related()
        return sum(filter(
            None, [t.outstanding_shares for t in certificates]))

    def proceeds(self, purchase_price):
        certificates = self.select_related()
        return sum(filter(
            None, [t.proceeds(purchase_price) for t in certificates]))



    def proforma(self, new_money, pre_valuation, pool_rata):
        """Calculate the price and share totals of a prospective financing.

        This function returns the number and price of new shares created
        as a result of a proposed financing.  Proposed financings require two
        variables:  the new money and pre-valuation.  For financings, this is
        generally called an "X on Y", where X represents the new cash and Y the
        pre-valuation.  This function assumes the standard case that all
        convertibles and options are "in the pre", meaning that they are
        considered as part of the determination of the share price.
        """

        # The post valuation is simply the prevaluation plus the new cash.
        post_valuation = pre_valuation + new_money

        # First, calculate what the new investors will expect in terms of
        # ownership after the financing has occured.
        new_rata = new_money / post_valuation

        # Calculate what the convertible investors will expect per the terms
        # of their debt instrument.
        discounted = self.exclude(status=STATUS_CONVERTED).discounted(pre_valuation)
        convert_rata = discounted / post_valuation

        # Add the available options for use in calculations and if
        # there is no option pool set to zero.
        available = self.available

        # Calculate the existing rata of granted shares; the pool must
        # be expanded by a concordimant amount to reach the desired
        # pool rata.
        pre_shares = self.outstanding_shares + self.outstanding_options + self.warrants

        # Aggregate the rata and determine the total expansion of
        # capital from the existing number of outstanding shares
        # and available option pool.
        combined_rata = new_rata + convert_rata + pool_rata
        if pool_rata:
            expansion = (combined_rata / (1-combined_rata)) * (pre_shares + 0)
        else:
            expansion = (combined_rata / (1-combined_rata)) * (pre_shares + available)

        # Ratably distribute shares such that everyone gets
        # what one expects to get.
        new_money_shares = (new_rata / combined_rata) * expansion
        new_converted_shares = (convert_rata / combined_rata) * expansion
        if pool_rata:
            new_pool_shares = expansion - new_money_shares - new_converted_shares - available
        else:
            new_pool_shares = 0

        # The prorata are the rights that existing investors have to
        # ratably buy into the next round should they wish to.  See
        # additional explanation and some caveats in the prorata
        # method below under the Transaction model.
        new_prorata_shares = self.prorata(new_money_shares)
        new_investor_shares = new_money_shares - new_prorata_shares

        # Finally, calculate the price of the new offering.
        new_price = new_money / new_money_shares

        return {
            'available': available,
            'pre_shares': pre_shares,
            'expansion': expansion,
            'new_rata': new_rata,
            'combined_rata': combined_rata,
            'new_money_shares': new_money_shares,
            'new_prorata_shares': new_prorata_shares,
            'new_converted_shares': new_converted_shares,
            'new_investor_shares': new_investor_shares,
            'new_pool_shares': new_pool_shares,
            'price': new_price,
        }


    def price(self, purchase_price):
        """Calculate the per-security purchase price in liquidation.

        In the event of a liquidation different classes of stock are
        treated differently.  This function produces the price of
        each security per the terms under which it was offered.
        """

        # First, gather all the transactions for this company.
        certificates = self.select_related()

        # Set the intial values for the variables that will be used
        # within the liquidation loop.
        residual_cash = purchase_price
        residual_shares = self.liquidated

        # Initiate the output variable
        price = {}

        # Determine the priority of the most senior security.
        security = get_model('captable', 'Security')
        x = security.objects.select_related().aggregate(
                t=Max('seniority'))['t']

        # We are now going to loop through all transactions, grouped
        # and ordered by security
        while x > 0:

            # Determine the price per share of whatever is left at
            # this point.  This number serves as the threshold for
            # all of the conditional logic.
            residual_price = residual_cash / residual_shares

            # Now filter for all the transactions at this level of seniority.
            tranch_transactions = certificates.filter(security__seniority=x)

            # And set the number of shares and preference accordingly.
            tranch_shares = tranch_transactions.liquidated
            tranch_preference = tranch_transactions.preference

            # We also need to check for participation, and set those
            # variables for the conditional logic.
            is_participating = False
            for t in tranch_transactions:
                if t.security.is_participating:
                    is_participating = True
                    participation_cap = t.security.participation_cap
                    break

            # With the prep work done, it's time for the core algorthm.

            # First we need to see if there's enough cash to cover the
            # current group of securities.  If not, then we rata out
            # whatever cash remains and stop distributing proceeds by
            # zeroing the price for all reamining securities.
            if tranch_preference > residual_cash:
                residual_price = residual_cash / tranch_shares
                price.update({x: residual_price})
                x -= 1
                while x > 0:
                    price.update({x: 0.0})
                    x -= 1
                break

            # Next, we consider participation.
            elif is_participating:

                # And check if there is a cap.
                if participation_cap:

                    # If the capped price is less than the residual price,
                    # the security will convert to common.
                    if (tranch_preference * participation_cap) / tranch_shares < residual_price:
                        while x > 0:
                            price.update({x: residual_price})
                            x -= 1
                        break

                    # If the price is below the cap then first set the preference,
                    # add the remaining residual pro-rata up to the cap,
                    # reduce the residual cash and shares accordingly,
                    # decrement the counter to indicate this group is finished,
                    # and send through the loop again to handle the remaining.
                    else:
                        part_price = sum([
                            tranch_preference / tranch_shares,
                            (residual_cash - tranch_preference) / residual_shares])
                        residual_cash -= part_price * tranch_shares
                        residual_shares -= tranch_shares
                        price.update({x: part_price})
                        x -= 1

                # If there is no cap then the security is fully
                # participating, which means it first takes the
                # preference and then the proceeds on top -- there
                # is no conversion because the investor can have
                # his cake and eat it too.  Avoid this term if you can.
                else:
                    part_price = sum([
                        tranch_preference / tranch_shares,
                        (residual_cash - tranch_preference) / residual_shares])
                    residual_cash -= tranch_preference
                    price.update({x: part_price})
                    x -= 1

            # if the overall price per share, diluted, exceeds the preference then
            # any preferred stock will convert to common, meaning
            # that all stock in all remaining tranches will be distributed
            # ratably.  We do need to check for the capped participation
            # condition first: there are cases where it's best to take
            # the capped participation preference rather than convert.
            # Note: it is assumed that if the top-preference converts
            # to common, then all subsequent securities convert to common.
            # If this isn't the case then the lawyers probably weren't doing
            # their job.
            elif (tranch_preference / tranch_shares) < residual_price:
                while x > 0:
                    price.update({x: residual_price})
                    x -= 1
                break

            # The final condition means there is enough cash to cover
            # the preference, but not enough to convert to common.  So,
            # give the current tranch the preference, reduce the number
            # of shares and cash remaining by what was distributed,
            # and send through the loop again.
            else:
                price.update({x: tranch_preference / tranch_shares})
                residual_cash -= tranch_preference
                residual_shares -= tranch_shares
                x -= 1

        return price
